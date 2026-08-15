"""任务队列。

简单的内存优先级队列。按 priority_rank 排序，同优先级按创建时间 FIFO。

TODO(queue): 后续可替换为 Redis/RabbitMQ 实现分布式队列。
参考：docs/specs/2026-08-15-cogrid-design.md §3
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Optional

from coordinator.models.task import Task, TaskStatus

logger = logging.getLogger(__name__)


class TaskQueue:
    """优先级任务队列。"""

    def __init__(self) -> None:
        # 按优先级分桶：高优先级桶先出
        self._high: deque[Task] = deque()    # USER_TASK 不可抢占
        self._mid: deque[Task] = deque()     # USER_TASK 可抢占
        self._probe: deque[Task] = deque()   # PROBE
        self._filler: deque[Task] = deque()  # FILLER
        self._all: dict[str, Task] = {}      # task_id -> Task（所有状态的快查）

    def enqueue(self, task: Task) -> None:
        """任务入队。"""
        task.status = TaskStatus.PENDING
        self._all[task.task_id] = task
        self._route(task).append(task)
        logger.info(f"Task enqueued: {task.task_id} (type={task.task_type}, priority={task.priority_rank})")

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
            task.started_at = __import__("time").time()
        if status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.PREEMPTED):
            task.completed_at = __import__("time").time()
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
        from coordinator.models.task import TaskType
        return [
            t for t in self._all.values()
            if t.status == TaskStatus.RUNNING and t.task_type == TaskType.FILLER
        ]

    def _route(self, task: Task) -> deque[Task]:
        """将任务路由到对应优先级桶。"""
        from coordinator.models.task import TaskType
        if task.task_type == TaskType.USER_TASK and not task.preemptible:
            return self._high
        elif task.task_type == TaskType.USER_TASK:
            return self._mid
        elif task.task_type == TaskType.PROBE:
            return self._probe
        else:
            return self._filler
