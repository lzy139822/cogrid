"""任务模型。"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from dataclasses import dataclass, field

from coordinator.models.node import Resources


class TaskType(str, Enum):
    """任务类型。

    - USER_TASK: 用户提交的计算任务（最高优先级）
    - PROBE: PoA 探针任务（验证节点在线）
    - FILLER: 填充任务（算力固存，最低优先级，可被抢占）
    """
    USER_TASK = "user_task"
    PROBE = "probe"
    FILLER = "filler"


class TaskStatus(str, Enum):
    """任务状态流转：pending → assigned → running → completed/failed/preempted。"""
    PENDING = "pending"       # 在队列中等待调度
    ASSIGNED = "assigned"     # 已分配给节点，等待 Agent 确认
    RUNNING = "running"       # Agent 正在执行
    COMPLETED = "completed"   # 成功完成
    FAILED = "failed"         # 执行失败
    PREEMPTED = "preempted"   # 被抢占，回队列等待重新调度


@dataclass
class Task:
    """一个计算任务。"""
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    user_id: str = ""              # 提交者（USER_TASK 才有）
    image: str = ""                # Docker 镜像
    command: list[str] = field(default_factory=list)  # 启动命令
    requirement: Resources = field(default_factory=Resources)  # 资源需求
    timeout_seconds: int = 3600    # 超时
    task_type: TaskType = TaskType.USER_TASK
    preemptible: bool = True       # 是否可被抢占
    priority: int = 0              # 优先级（越大越优先）
    status: TaskStatus = TaskStatus.PENDING
    assigned_node: str = ""        # 被分配到的节点 ID
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0
    # 填充任务特有：产物描述
    artifact_desc: str = ""

    @property
    def is_preemptible(self) -> bool:
        """是否可被抢占。USER_TASK 中不可抢占的除外，FILLER 始终可抢占。"""
        if self.task_type == TaskType.FILLER:
            return True
        return self.preemptible

    @property
    def priority_rank(self) -> int:
        """调度优先级排名（越大越优先）。

        USER_TASK(不可抢占) > USER_TASK(可抢占) > PROBE > FILLER
        """
        base = {
            TaskType.USER_TASK: 100,
            TaskType.PROBE: 50,
            TaskType.FILLER: 10,
        }[self.task_type]
        bonus = 50 if (self.task_type == TaskType.USER_TASK and not self.preemptible) else 0
        return base + bonus + self.priority


@dataclass
class TaskAssignment:
    """调度器分配给节点的任务。"""
    task_id: str
    image: str
    command: list[str]
    requirement: Resources
    timeout_seconds: int
    task_type: TaskType
    preemptible: bool


@dataclass
class TaskResult:
    """任务执行结果。"""
    task_id: str
    success: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    actual_usage: Resources = field(default_factory=Resources)
    artifact_path: str = ""  # 填充任务产物路径
