"""按比例分红调度器。

核心调度逻辑：
1. 用户任务按提交者份额比例分配优先级
2. 份额大的用户优先获得更多并发资源
3. 超出份额的部分走弹性池（优先级降级）
4. 空闲时派发填充任务（算力固存）
5. 贡献者需要资源时可抢占弹性任务和填充任务

抢占回收（参考：docs/specs/2026-08-15-cogrid-design.md §2.1 抢占回收）：
- preempt_for_owner:           贡献者回收自己节点上他人的弹性任务
- preempt_low_priority_for_high_priority: 高优先级任务抢占低优先级弹性任务
- check_and_preempt:            后台自动检查并触发抢占

抢占优先级：填充任务 > 弹性用户任务 > 份额内用户任务。
抢占是优雅的：先标记 PREEMPTED 让 Agent 保存 checkpoint，
再强制停止并重新入队等待调度。
"""

from __future__ import annotations

import logging
from typing import Optional

from coordinator.models.node import Node, NodeStatus, Resources
from coordinator.models.task import Task, TaskStatus, TaskType
from coordinator.queue import TaskQueue
from coordinator.ledger import Ledger

logger = logging.getLogger(__name__)

# 基础保障额度：份额为零的用户也能获得的最低算力比例
BASELINE_GUARANTEE = 0.05

# 优雅抢占的宽限时间（秒）。调度器标记抢占后，Agent 有这段时间保存
# checkpoint 再强制停止。此常量供 Agent 侧 executor 引用；调度器本身
# 的抢占逻辑是同步的，实际的等待发生在 Agent 端。
PREEMPT_GRACE_SECONDS = 3


class Scheduler:
    """按比例分红调度器。"""

    def __init__(self, queue: TaskQueue, ledger: Ledger) -> None:
        self.queue = queue
        self.ledger = ledger
        self._nodes: dict[str, Node] = {}

    def register_node(self, node: Node, owner_user_id: str = "") -> None:
        """注册节点。

        Args:
            node: 节点实例
            owner_user_id: 节点归属用户 ID。若 node 已设置 owner_user_id 则优先使用；
                          否则用此参数设置。多租户场景下标识节点由哪位贡献者注册。
        """
        if owner_user_id and not node.owner_user_id:
            node.owner_user_id = owner_user_id
        self._nodes[node.node_id] = node
        self.ledger.get_or_create(node.node_id)
        logger.info(
            f"Node registered: {node.name} ({node.node_id}) "
            f"owner={node.owner_user_id or 'anonymous'}"
        )

    def update_node_heartbeat(self, node_id: str, resources: Resources, intensity: str) -> Optional[Node]:
        """更新节点心跳。"""
        node = self._nodes.get(node_id)
        if node is None:
            return None
        node.resources = resources
        from coordinator.models.node import IntensityLevel
        node.intensity = IntensityLevel(intensity) if intensity else node.intensity
        node.touch()
        node.status = NodeStatus.ONLINE
        # 结算在线贡献分
        self.ledger.settle_online_credits(node)
        return node

    def check_node_timeout(self) -> list[str]:
        """检查节点超时，返回刚离线的节点 ID 列表。"""
        offline_ids = []
        for node in self._nodes.values():
            if node.status == NodeStatus.ONLINE and not node.is_online:
                node.status = NodeStatus.OFFLINE
                offline_ids.append(node.node_id)
                logger.warning(f"Node timed out: {node.name} ({node.node_id})")
        return offline_ids

    def get_online_nodes(self) -> list[Node]:
        """获取所有在线节点。"""
        return [n for n in self._nodes.values() if n.is_online]

    def get_node(self, node_id: str) -> Optional[Node]:
        """获取节点。"""
        return self._nodes.get(node_id)

    def get_all_nodes(self) -> list[Node]:
        """获取所有节点。"""
        return list(self._nodes.values())

    def schedule_next(self) -> Optional[tuple[Task, Node]]:
        """调度下一个任务。

        返回 (task, node) 或 None。
        优先级：USER_TASK(不可抢占) > USER_TASK(可抢占) > PROBE > FILLER
        """
        online_nodes = self.get_online_nodes()
        if not online_nodes:
            return None

        while True:
            task = self.queue.dequeue()
            if task is None:
                return None

            # 找一个有足够资源的节点
            node = self._find_node_for_task(task, online_nodes)
            if node:
                task.status = TaskStatus.ASSIGNED
                task.assigned_node = node.node_id
                node.running_tasks.append(task.task_id)
                # 记录该任务占用的份额归属用户，供抢占回收判断
                node.shares_allocated[task.task_id] = task.user_id
                logger.info(
                    f"Scheduled task {task.task_id} (type={task.task_type}) "
                    f"to node {node.name}"
                )
                return (task, node)
            # 没有合适节点，任务放回队列尾部
            self.queue.enqueue(task)
            # 避免无限循环：如果队列里只有放不下的任务
            if self.queue.get_pending_count() <= 1:
                return None

    def _find_node_for_task(self, task: Task, nodes: list[Node]) -> Optional[Node]:
        """为任务寻找有足够资源的节点。

        对于 USER_TASK：按用户份额比例排序节点，份额大的节点优先。
        对于 PROBE/FILLER：找任意空闲节点。
        """
        # 过滤有足够资源的节点
        candidates = []
        for node in nodes:
            avail = node.available_resources()
            if (avail.cpu_cores_total >= task.requirement.cpu_cores_total
                    and avail.gpu_count_total >= task.requirement.gpu_count_total
                    and avail.memory_mb_total >= task.requirement.memory_mb_total):
                candidates.append(node)

        if not candidates:
            return None

        if task.task_type == TaskType.USER_TASK:
            # 按节点贡献者份额排序（份额大的优先承担，回馈贡献者）
            candidates.sort(
                key=lambda n: self.ledger.get_share_ratio(n.node_id),
                reverse=True
            )
        else:
            # PROBE/FILLER：选当前负载最低的
            candidates.sort(key=lambda n: len(n.running_tasks))

        return candidates[0]

    def task_completed(self, task_id: str, success: bool) -> None:
        """任务完成回调。"""
        task = self.queue.get(task_id)
        if task is None:
            return
        status = TaskStatus.COMPLETED if success else TaskStatus.FAILED
        self.queue.update_status(task_id, status)
        # 从节点的运行列表中移除
        node = self._nodes.get(task.assigned_node)
        if node and task_id in node.running_tasks:
            node.running_tasks.remove(task_id)
        if node:
            node.shares_allocated.pop(task_id, None)
        # 任务已完成，清除陈旧 checkpoint
        task.checkpoint_data = ""
        logger.info(f"Task {task_id} completed: success={success}")

    def preempt_fillers_for_user_task(self) -> int:
        """为用户任务抢占填充任务。

        当用户任务在排队但没有空闲节点时，抢占填充任务释放资源。
        返回抢占的任务数。
        """
        filler_running = self.queue.get_filler_running()
        count = 0
        for task in filler_running:
            self.queue.update_status(task.task_id, TaskStatus.PREEMPTED)
            node = self._nodes.get(task.assigned_node)
            if node and task.task_id in node.running_tasks:
                node.running_tasks.remove(task.task_id)
            # 重新入队等待调度
            task.status = TaskStatus.PENDING
            task.assigned_node = ""
            self.queue.enqueue(task)
            count += 1
            logger.info(f"Preempted filler task {task.task_id} for user tasks")
        return count

    # ------------------------------------------------------------------
    # 抢占回收（参考：docs/specs/2026-08-15-cogrid-design.md §2.1）
    # ------------------------------------------------------------------

    def _preempt_task(self, task: Task) -> None:
        """抢占单个任务（内部辅助方法）。

        优雅抢占流程：
        1. 标记任务为 PREEMPTED —— Agent 据此感知并尝试保存 checkpoint。
           实际的宽限等待（PREEMPT_GRACE_SECONDS）发生在 Agent 侧 executor，
           调度器只负责状态流转与簿记。
        2. 从节点 running_tasks 和 shares_allocated 中移除该任务。
        3. 通过 queue.requeue() 重新入队，等待重新调度。

        checkpoint 保留：任务已有的 checkpoint_data 随 Task 对象一起保留，
        CheckpointManager 在重新调度时加载，交给 Agent 续跑。
        """
        # 1. 标记为 PREEMPTED（触发 Agent 侧的优雅停止逻辑）
        self.queue.update_status(task.task_id, TaskStatus.PREEMPTED)

        # 2. 从节点簿记中移除
        node = self._nodes.get(task.assigned_node)
        if node:
            if task.task_id in node.running_tasks:
                node.running_tasks.remove(task.task_id)
            node.shares_allocated.pop(task.task_id, None)

        # 3. 重新入队等待重新调度
        self.queue.requeue(task.task_id)

        logger.info(
            "Preempted task %s (type=%s, user=%s)",
            task.task_id,
            task.task_type,
            task.user_id,
        )

    def _resource_satisfied(
        self, freed_cpu: int, freed_gpu: int, freed_memory: int,
        req_cpu: int, req_gpu: int, req_memory: int,
    ) -> bool:
        """判断已释放的资源是否满足需求。"""
        return (
            freed_cpu >= req_cpu
            and freed_gpu >= req_gpu
            and freed_memory >= req_memory
        )

    def _get_user_share_ratio(self, user_id: str) -> float:
        """计算用户的份额比例。

        用户份额 = 其拥有的所有节点的贡献分之和 / 全网总贡献分。
        纯消费用户（无贡献节点）份额为 0，抢占时优先被让出。
        """
        total = self.ledger.total_credits
        if total <= 0:
            return 0.0
        user_credits = sum(
            self.ledger.get_credits(n.node_id)
            for n in self._nodes.values()
            if n.owner_user_id == user_id
        )
        return user_credits / total

    def preempt_for_owner(
        self,
        owner_user_id: str,
        required_cpu: int = 0,
        required_gpu: int = 0,
        required_memory: int = 0,
    ) -> int:
        """贡献者回收份额。

        当 owner 提交任务但没有空闲资源时，检查该 owner 的节点上是否有
        其他用户的弹性任务在跑。如果有，抢占这些任务释放资源给 owner。
        返回抢占的任务数。

        Args:
            owner_user_id:  贡献者（节点归属）用户 ID
            required_cpu:   需要的 CPU 核数（0 表示不限）
            required_gpu:   需要的 GPU 卡数
            required_memory: 需要的内存 (MB)

        Returns:
            被抢占的任务数
        """
        count = 0
        freed_cpu = 0
        freed_gpu = 0
        freed_memory = 0

        # 遍历 owner 拥有的在线节点
        owner_nodes = [
            n for n in self.get_online_nodes()
            if n.owner_user_id == owner_user_id
        ]

        for node in owner_nodes:
            # 资源已满足需求则停止
            if self._resource_satisfied(
                freed_cpu, freed_gpu, freed_memory,
                required_cpu, required_gpu, required_memory,
            ):
                break

            # 检查节点上运行的任务（复制列表避免抢占时并发修改）
            for task_id in list(node.running_tasks):
                if self._resource_satisfied(
                    freed_cpu, freed_gpu, freed_memory,
                    required_cpu, required_gpu, required_memory,
                ):
                    break

                task = self.queue.get(task_id)
                if task is None:
                    continue
                # 只抢占其他用户的弹性任务（非 owner 自己的任务）
                if task.user_id == owner_user_id:
                    continue
                if not task.is_preemptible:
                    continue

                self._preempt_task(task)
                freed_cpu += task.requirement.cpu_cores_total
                freed_gpu += task.requirement.gpu_count_total
                freed_memory += task.requirement.memory_mb_total
                count += 1

        if count:
            logger.info(
                "Owner %s reclaimed shares: preempted %d tasks, "
                "freed cpu=%d gpu=%d memory=%dMB",
                owner_user_id,
                count,
                freed_cpu,
                freed_gpu,
                freed_memory,
            )
        return count

    def preempt_low_priority_for_high_priority(self, high_task: Task) -> int:
        """高优先级任务抢占低优先级任务。

        当高优先级用户任务排队但没有空闲节点时，抢占低优先级的弹性任务。
        抢占顺序：FILLER 优先，然后是低份额用户的可抢占 USER_TASK。

        Args:
            high_task: 需要资源的高优先级任务

        Returns:
            被抢占的任务数
        """
        # 获取所有运行中的可抢占任务
        candidates = self.queue.get_preemptible_running()

        # 按优先级排序：FILLER 优先抢占（type_order=0），然后是低份额用户
        # 的可抢占 USER_TASK（type_order=1，按用户份额升序——份额越低越先被抢占）
        def sort_key(task: Task) -> tuple[int, float]:
            type_order = 0 if task.task_type == TaskType.FILLER else 1
            user_share = self._get_user_share_ratio(task.user_id)
            return (type_order, user_share)

        candidates.sort(key=sort_key)

        req_cpu = high_task.requirement.cpu_cores_total
        req_gpu = high_task.requirement.gpu_count_total
        req_memory = high_task.requirement.memory_mb_total

        count = 0
        freed_cpu = 0
        freed_gpu = 0
        freed_memory = 0

        for task in candidates:
            if self._resource_satisfied(
                freed_cpu, freed_gpu, freed_memory,
                req_cpu, req_gpu, req_memory,
            ):
                break

            self._preempt_task(task)
            freed_cpu += task.requirement.cpu_cores_total
            freed_gpu += task.requirement.gpu_count_total
            freed_memory += task.requirement.memory_mb_total
            count += 1

        if count:
            logger.info(
                "High priority task %s preempted %d low priority tasks "
                "(freed cpu=%d gpu=%d memory=%dMB)",
                high_task.task_id,
                count,
                freed_cpu,
                freed_gpu,
                freed_memory,
            )
        return count

    def check_and_preempt(self) -> int:
        """自动检查是否需要抢占。

        遍历待处理的高优先级任务，如果有排队且有可抢占任务在跑，触发抢占。
        策略：
        1. 先抢占 FILLER（最低优先级，可无损让出）
        2. 若仍有 pending USER_TASK 且有可抢占弹性任务，继续抢占低优先级弹性任务

        Returns:
            本次检查抢占的任务总数
        """
        total = 0

        # 检查 pending 的 USER_TASK
        pending_user_tasks = [
            t for t in self.queue.get_all_tasks()
            if t.status == TaskStatus.PENDING
            and t.task_type == TaskType.USER_TASK
        ]
        if not pending_user_tasks:
            return 0

        # 如果有 pending USER_TASK 且有 running FILLER，抢占 FILLER
        filler_running = self.queue.get_filler_running()
        if filler_running:
            total += self.preempt_fillers_for_user_task()

        # 抢占 FILLER 后若仍有 pending USER_TASK 且有可抢占弹性任务，
        # 继续抢占低优先级弹性用户任务
        still_pending = [
            t for t in self.queue.get_all_tasks()
            if t.status == TaskStatus.PENDING
            and t.task_type == TaskType.USER_TASK
        ]
        for task in still_pending:
            # 没有可抢占任务在跑了，停止
            if not self.queue.get_preemptible_running():
                break
            pre = self.preempt_low_priority_for_high_priority(task)
            total += pre
            if pre == 0:
                # 本次没有抢占到任何任务，说明没有更多可抢占的候选
                break

        if total:
            logger.info(
                "Auto-preempt: %d tasks preempted for pending user tasks",
                total,
            )
        return total

    def get_pool_status(self) -> dict:
        """获取算力池状态概览。

        包含全网汇总和按 owner 分组的统计（多租户可见各自节点资源）。
        """
        online = self.get_online_nodes()
        total_cpu = sum(n.available_resources().cpu_cores_total for n in online)
        total_gpu = sum(n.available_resources().gpu_count_total for n in online)
        total_memory = sum(n.available_resources().memory_mb_total for n in online)

        # 按 owner 分组统计（多租户：每个贡献者的节点与资源量）
        by_owner: dict[str, dict] = {}
        for node in self._nodes.values():
            owner = node.owner_user_id or "anonymous"
            if owner not in by_owner:
                by_owner[owner] = {
                    "total_nodes": 0,
                    "online_nodes": 0,
                    "available_cpu_cores": 0,
                    "available_gpu_count": 0,
                    "available_memory_mb": 0,
                }
            by_owner[owner]["total_nodes"] += 1
            if node.is_online:
                by_owner[owner]["online_nodes"] += 1
                avail = node.available_resources()
                by_owner[owner]["available_cpu_cores"] += avail.cpu_cores_total
                by_owner[owner]["available_gpu_count"] += avail.gpu_count_total
                by_owner[owner]["available_memory_mb"] += avail.memory_mb_total

        return {
            "online_nodes": len(online),
            "total_nodes": len(self._nodes),
            "available_cpu_cores": total_cpu,
            "available_gpu_count": total_gpu,
            "available_memory_mb": total_memory,
            "pending_tasks": self.queue.get_pending_count(),
            "running_tasks": self.queue.get_running_count(),
            "total_credits": round(self.ledger.total_credits, 2),
            "by_owner": by_owner,
        }
