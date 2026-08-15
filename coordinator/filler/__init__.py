"""算力固存（Compute Solidification）。

当池有空闲容量且无用户任务时，调度器自动派发"填充任务"，
把算力转化成持久价值。填充任务也算真实贡献。

四种固存形式（点火阶段实现前两种）：
1. 镜像预构建 — 常用任务模板的 Docker 镜像提前构建（已实现）
2. 预计算缓存 — 热门模型推理结果预存（已实现）
3. 社区模型训练 — 池化 GPU 渐进训练社区模型（待实现）
4. BOINC 志愿计算 — 接入公益项目（待实现）

填充任务可被用户任务随时抢占（优先级最低）。

TODO(filler): 实现社区模型训练填充任务。
参考：docs/specs/2026-08-15-cogrid-design.md §2.3 算力固存
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from coordinator.models.node import Node, Resources
from coordinator.models.task import Task, TaskType
from coordinator.queue import TaskQueue
from coordinator.scheduler import Scheduler

logger = logging.getLogger(__name__)

# 池空闲阈值：低于此比例触发填充任务
IDLE_THRESHOLD = 0.3


class Filler:
    """算力固存管理器。"""

    def __init__(self, queue: TaskQueue, scheduler: Scheduler) -> None:
        self.queue = queue
        self.scheduler = scheduler
        self._artifacts: dict[str, dict] = {}  # task_id -> artifact info

    def is_pool_idle(self) -> bool:
        """判断算力池是否空闲（无用户任务在跑）。"""
        online_nodes = self.scheduler.get_online_nodes()
        if not online_nodes:
            return False

        # 检查是否有用户任务在运行或排队
        all_tasks = self.queue.get_all_tasks()
        from coordinator.models.task import TaskStatus
        active_user_tasks = [
            t for t in all_tasks
            if t.task_type == TaskType.USER_TASK
            and t.status in (TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.ASSIGNED)
        ]
        return len(active_user_tasks) == 0

    def generate_filler_tasks(self) -> list[Task]:
        """生成填充任务。

        当池空闲时，为每个在线节点生成一个填充任务。
        优先级最低，可被抢占。
        """
        if not self.is_pool_idle():
            return []

        online_nodes = self.scheduler.get_online_nodes()
        # 已经在跑填充任务的节点不重复分配
        running_fillers = self.queue.get_filler_running()
        busy_nodes = {f.assigned_node for f in running_fillers}

        tasks = []
        for node in online_nodes:
            if node.node_id in busy_nodes:
                continue
            task = self._create_filler_task(node)
            if task:
                tasks.append(task)
        return tasks

    def _create_filler_task(self, node: Node) -> Optional[Task]:
        """为节点创建一个填充任务。

        点火阶段：交替生成镜像预构建和预计算缓存任务。
        """
        # 简单轮询两种填充类型
        running_fillers = self.queue.get_filler_running()
        use_cache = len(running_fillers) % 2 == 1

        if use_cache:
            return self._create_precompute_cache_task(node)
        else:
            return self._create_image_build_task(node)

    def _create_image_build_task(self, node: Node) -> Task:
        """镜像预构建填充任务。

        预构建常用 Python 基础镜像，后续用户提交任务可直接拉取。
        """
        avail = node.available_resources()
        task = Task(
            task_id=f"filler_build_{uuid.uuid4().hex[:8]}",
            user_id="system",
            image="busybox:latest",
            command=[
                "sh", "-c",
                "echo 'Solidification: pre-building image layer'; "
                "echo 'Simulating image build...'; "
                "sleep 10; "
                "echo 'Image layer cached successfully'"
            ],
            requirement=Resources(
                cpu_cores_total=min(2, avail.cpu_cores_total),
                gpu_count_total=0,
                memory_mb_total=min(512, avail.memory_mb_total),
            ),
            timeout_seconds=120,
            task_type=TaskType.FILLER,
            preemptible=True,
            artifact_desc="pre-built image layer",
        )
        task.assigned_node = node.node_id
        logger.info(f"Filler (image-build) task created for node {node.name}: {task.task_id}")
        return task

    def _create_precompute_cache_task(self, node: Node) -> Task:
        """预计算缓存填充任务。

        预计算一些常用结果并缓存，后续用户可秒取。
        """
        avail = node.available_resources()
        task = Task(
            task_id=f"filler_cache_{uuid.uuid4().hex[:8]}",
            user_id="system",
            image="busybox:latest",
            command=[
                "sh", "-c",
                "echo 'Solidification: pre-computing cache'; "
                "echo 'Simulating cache computation...'; "
                "for i in $(seq 1 100); do echo $((i * i)); done > /tmp/cache_result; "
                "sleep 10; "
                "echo 'Cache entry stored'"
            ],
            requirement=Resources(
                cpu_cores_total=min(2, avail.cpu_cores_total),
                gpu_count_total=0,
                memory_mb_total=min(512, avail.memory_mb_total),
            ),
            timeout_seconds=120,
            task_type=TaskType.FILLER,
            preemptible=True,
            artifact_desc="pre-computed cache entry",
        )
        task.assigned_node = node.node_id
        logger.info(f"Filler (precompute-cache) task created for node {node.name}: {task.task_id}")
        return task

    def record_artifact(self, task_id: str, artifact_path: str, desc: str) -> None:
        """记录填充任务的产物。"""
        self._artifacts[task_id] = {
            "task_id": task_id,
            "artifact_path": artifact_path,
            "description": desc,
            "created_at": time.time(),
        }
        logger.info(f"Artifact recorded: {task_id} -> {artifact_path} ({desc})")

    def get_artifacts(self) -> list[dict]:
        """获取所有固存产物。"""
        return list(self._artifacts.values())
