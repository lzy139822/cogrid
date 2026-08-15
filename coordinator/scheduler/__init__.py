"""按比例分红调度器。

核心调度逻辑：
1. 用户任务按提交者份额比例分配优先级
2. 份额大的用户优先获得更多并发资源
3. 超出份额的部分走弹性池（优先级降级）
4. 空闲时派发填充任务（算力固存）
5. 贡献者需要资源时可抢占弹性任务和填充任务

TODO(scheduler): 实现抢占式回收，当前只支持填充任务让出。
下一步：在 preempt_for_owner() 中加份额主人优先级判断。
参考：docs/specs/2026-08-15-cogrid-design.md §2.1 抢占回收
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from coordinator.models.node import Node, NodeStatus, Resources
from coordinator.models.task import Task, TaskStatus, TaskType, TaskAssignment
from coordinator.queue import TaskQueue
from coordinator.ledger import Ledger

logger = logging.getLogger(__name__)

# 基础保障额度：份额为零的用户也能获得的最低算力比例
BASELINE_GUARANTEE = 0.05


class Scheduler:
    """按比例分红调度器。"""

    def __init__(self, queue: TaskQueue, ledger: Ledger) -> None:
        self.queue = queue
        self.ledger = ledger
        self._nodes: dict[str, Node] = {}

    def register_node(self, node: Node) -> None:
        """注册节点。"""
        self._nodes[node.node_id] = node
        self.ledger.get_or_create(node.node_id)
        logger.info(f"Node registered: {node.name} ({node.node_id})")

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

    def get_pool_status(self) -> dict:
        """获取算力池状态概览。"""
        online = self.get_online_nodes()
        total_cpu = sum(n.available_resources().cpu_cores_total for n in online)
        total_gpu = sum(n.available_resources().gpu_count_total for n in online)
        total_memory = sum(n.available_resources().memory_mb_total for n in online)
        return {
            "online_nodes": len(online),
            "total_nodes": len(self._nodes),
            "available_cpu_cores": total_cpu,
            "available_gpu_count": total_gpu,
            "available_memory_mb": total_memory,
            "pending_tasks": self.queue.get_pending_count(),
            "running_tasks": self.queue.get_running_count(),
            "total_credits": round(self.ledger.total_credits, 2),
        }
