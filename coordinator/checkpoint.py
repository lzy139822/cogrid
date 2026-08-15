"""任务 checkpoint 管理器。

在抢占式调度中，当弹性任务（填充任务 / 低优先级用户任务）被贡献者
或高优先级任务抢占时，如果能保存任务进度（checkpoint），重新调度时
即可从断点续跑，避免从头重算造成的算力浪费。

CheckpointManager 是一个轻量封装，checkpoint 数据直接存储在 Task 对象的
``checkpoint_data`` 字段上（内存态），并借助 TaskQueue 的持久化通道落盘。
这样设计的好处：
- 无需单独维护一张 checkpoint 表，任务状态与 checkpoint 同生命周期。
- 纯内存场景（无 Storage）也能工作，方便单元测试。

参考：docs/specs/2026-08-15-cogrid-design.md §2.1 抢占回收、§3.1 抢占回收
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from coordinator.queue import TaskQueue

logger = logging.getLogger(__name__)


class CheckpointManager:
    """任务 checkpoint 管理器。

    用法::

        ckpt = CheckpointManager(queue)
        ckpt.save_checkpoint("task_1", '{"step": 42}')  # 抢占前保存
        ...
        data = ckpt.load_checkpoint("task_1")            # 重新调度时加载
        if data:
            # 从断点续跑
    """

    def __init__(self, queue: "TaskQueue") -> None:
        self.queue = queue

    def save_checkpoint(self, task_id: str, data: str) -> bool:
        """保存任务的 checkpoint 数据。

        在抢占发生前调用：把 Agent 回传的进度快照写入 Task.checkpoint_data，
        并标记 ``can_checkpoint=True``，使后续调度能识别该任务支持续跑。

        Args:
            task_id: 任务 ID
            data:    checkpoint 数据（通常是序列化后的字符串，如 JSON）

        Returns:
            True 表示保存成功；False 表示任务不存在。
        """
        task = self.queue.get(task_id)
        if task is None:
            logger.warning("保存 checkpoint 失败：任务 %s 不存在", task_id)
            return False
        task.checkpoint_data = data
        task.can_checkpoint = True
        # 借助队列的持久化通道落盘（无 Storage 时安全跳过）
        self.queue._persist(task)
        logger.info(
            "已保存任务 %s 的 checkpoint（%d 字节）", task_id, len(data)
        )
        return True

    def load_checkpoint(self, task_id: str) -> Optional[str]:
        """加载任务的 checkpoint 数据。

        在任务重新调度、下发到 Agent 前调用：如果有 checkpoint，
        把它随任务一起下发，Agent 据此续跑。

        Args:
            task_id: 任务 ID

        Returns:
            checkpoint 数据字符串；无 checkpoint 或任务不存在时返回 None。
        """
        task = self.queue.get(task_id)
        if task is None:
            return None
        if task.checkpoint_data:
            return task.checkpoint_data
        return None

    def clear_checkpoint(self, task_id: str) -> bool:
        """清除任务的 checkpoint 数据。

        任务成功完成或彻底失败后调用，避免陈旧 checkpoint 干扰后续调度。

        Returns:
            True 表示清除成功；False 表示任务不存在。
        """
        task = self.queue.get(task_id)
        if task is None:
            return False
        task.checkpoint_data = ""
        self.queue._persist(task)
        logger.info("已清除任务 %s 的 checkpoint", task_id)
        return True
