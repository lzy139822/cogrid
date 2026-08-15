"""Cogrid 数据模型定义。

所有模块通过这些模型通信。接口先行：即使实现是 stub，
模型定义也必须完整，让后来者知道数据结构。
"""

from coordinator.models.node import Node, NodeStatus, Resources
from coordinator.models.task import Task, TaskStatus, TaskType, TaskAssignment, TaskResult
from coordinator.models.contribution import ContributionRecord
from coordinator.models.user import User, UserRole

__all__ = [
    "Node", "NodeStatus", "Resources",
    "Task", "TaskStatus", "TaskType", "TaskAssignment", "TaskResult",
    "ContributionRecord",
    "User", "UserRole",
]
