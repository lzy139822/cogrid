"""任务队列。

简单的内存优先级队列。按 priority_rank 排序，同优先级按创建时间 FIFO。

持久化：构造函数接收可选的 Storage 实例。传入后，enqueue / update_status
等写操作会异步保存任务状态到 SQLite；不传入时退化为纯内存模式，
确保单元测试与轻量场景不受影响。

TODO(queue): 后续可替换为 Redis/RabbitMQ 实现分布式队列。
参考：docs/specs/2026-08-15-cogrid-design.md §3
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import TYPE_CHECKING, Optional

from coordinator.models.node import Resources
from coordinator.models.task import Task, TaskStatus, TaskType

if TYPE_CHECKING:
    from coordinator.storage import Storage

logger = logging.getLogger(__name__)


class TaskQueue:
    """优先级任务队列。"""

    def __init__(self, storage: "Optional[Storage]" = None) -> None:
        # 按优先级分桶：高优先级桶先出
        self._high: deque[Task] = deque()    # USER_TASK 不可抢占
        self._mid: deque[Task] = deque()     # USER_TASK 可抢占
        self._probe: deque[Task] = deque()   # PROBE
        self._filler: deque[Task] = deque()  # FILLER
        self._all: dict[str, Task] = {}      # task_id -> Task（所有状态的快查）
        self._storage = storage

    def _persist(self, task: Task) -> None:
        """将任务异步写入 SQLite（fire-and-forget）。

        - 未注入 Storage 时直接返回（纯内存模式）。
        - 在没有运行中事件循环时（如同步单元测试）安全跳过。
        """
        if self._storage is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 没有运行中的事件循环，跳过异步持久化
            return
        fut = loop.create_task(self._storage.save_task(task))

        def _on_done(t: "asyncio.Future") -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.warning(f"持久化任务失败 (task={task.task_id}): {exc}")

        fut.add_done_callback(_on_done)

    def enqueue(self, task: Task) -> None:
        """任务入队。"""
        task.status = TaskStatus.PENDING
        self._all[task.task_id] = task
        self._route(task).append(task)
        logger.info(f"Task enqueued: {task.task_id} (type={task.task_type}, priority={task.priority_rank})")
        self._persist(task)

    def dequeue(self) -> Optional[Task]:
        """按优先级取出下一个任务。"""
        for q in [self._high, self._mid, self._probe, self._filler]:
            while q:
                task = q.popleft()
                if task.task_id in self._all and self._all[task.task_id].status == TaskStatus.PENDING:
                    return task
                # 跳过已不在 PENDING 状态的任务
        return None

    def peek(self) -> Optional[Task]:
        """查看下一个任务但不取出。"""
        task = self.dequeue()
        if task:
            self._route(task).appendleft(task)
        return task

    def remove(self, task_id: str) -> Optional[Task]:
        """从队列中移除任务。"""
        if task_id in self._all:
            task = self._all.pop(task_id)
            task.status = TaskStatus.FAILED
            self._persist(task)
            return task
        return None

    def get(self, task_id: str) -> Optional[Task]:
        """按 ID 查找任务。"""
        return self._all.get(task_id)

    def update_status(self, task_id: str, status: TaskStatus, node_id: str = "") -> bool:
        """更新任务状态。"""
        task = self._all.get(task_id)
        if task is None:
            return False
        task.status = status
        if node_id:
            task.assigned_node = node_id
        if status == TaskStatus.RUNNING and task.started_at == 0:
            task.started_at = time.time()
        if status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.PREEMPTED):
            task.completed_at = time.time()
        self._persist(task)
        return True

    def get_preemptible_running(self) -> list[Task]:
        """获取所有可抢占的运行中任务（用于抢占回收）。"""
        return [
            t for t in self._all.values()
            if t.status == TaskStatus.RUNNING and t.is_preemptible
        ]

    def get_pending_count(self) -> int:
        """获取等待中的任务数。"""
        return sum(1 for t in self._all.values() if t.status == TaskStatus.PENDING)

    def get_running_count(self) -> int:
        """获取运行中的任务数。"""
        return sum(1 for t in self._all.values() if t.status == TaskStatus.RUNNING)

    def get_all_tasks(self) -> list[Task]:
        """获取所有任务。"""
        return list(self._all.values())

    def get_filler_running(self) -> list[Task]:
        """获取运行中的填充任务。"""
        return [
            t for t in self._all.values()
            if t.status == TaskStatus.RUNNING and t.task_type == TaskType.FILLER
        ]

    def _route(self, task: Task) -> deque[Task]:
        """将任务路由到对应优先级桶。"""
        if task.task_type == TaskType.USER_TASK and not task.preemptible:
            return self._high
        elif task.task_type == TaskType.USER_TASK:
            return self._mid
        elif task.task_type == TaskType.PROBE:
            return self._probe
        else:
            return self._filler

    async def load_from_storage(self) -> None:
        """启动时从 SQLite 加载未完成任务，恢复内存队列。

        - 未注入 Storage 时为空操作。
        - 仅加载未终结（非 completed / failed）的任务。
        - 重启前处于 running / assigned 的任务，其原分配节点已失效，
          重置为 PENDING 重新参与调度。
        """
        if self._storage is None:
            return
        tasks = await self._storage.load_tasks()
        loaded = 0
        for t in tasks:
            status = t["status"]
            # 终结态任务不恢复
            if status in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value):
                continue
            task = Task(
                task_id=t["task_id"],
                user_id=t["user_id"],
                image=t["image"],
                command=t["command"],
                requirement=Resources(
                    cpu_cores_total=t["cpu_cores"],
                    gpu_count_total=t["gpu_count"],
                    memory_mb_total=t["memory_mb"],
                ),
                timeout_seconds=t["timeout_seconds"],
                task_type=TaskType(t["task_type"]),
                preemptible=t["preemptible"],
                priority=t["priority"],
                status=TaskStatus(status),
                assigned_node=t["assigned_node"],
                created_at=t["created_at"],
                started_at=t["started_at"],
                completed_at=t["completed_at"],
                artifact_desc=t["artifact_desc"],
            )
            # 重启后原 running/assigned 的分配已失效，回到队列重新调度
            if task.status in (TaskStatus.RUNNING, TaskStatus.ASSIGNED):
                task.status = TaskStatus.PENDING
                task.assigned_node = ""
                task.started_at = 0.0
            self._all[task.task_id] = task
            self._route(task).append(task)
            loaded += 1
        logger.info(f"从存储加载了 {loaded} 个未完成任务")
