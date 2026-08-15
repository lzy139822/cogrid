"""PoA 探针（Proof of Availability）。

定期向空闲节点发送轻量探针任务，验证节点真的在线、资源真实可用。
探针成功 = 证明节点在贡献算力，即使没有用户任务也能累积"已验证贡献"。

探针任务极轻：几秒内完成的小计算（如矩阵乘、哈希计算）。
不影响本地使用。

TODO(prober): 当前探针频率固定 5 分钟，后续可按负载动态调整。
参考：docs/specs/2026-08-15-cogrid-design.md §2.3 PoA 探针
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from coordinator.models.node import Node, Resources
from coordinator.models.task import Task, TaskType
from coordinator.ledger import Ledger
from coordinator.queue import TaskQueue

logger = logging.getLogger(__name__)

# 探针间隔（秒）
PROBE_INTERVAL = 300  # 5 分钟
# 探针超时（秒）
PROBE_TIMEOUT = 30


class Prober:
    """PoA 探针管理器。"""

    def __init__(self, queue: TaskQueue, ledger: Ledger) -> None:
        self.queue = queue
        self.ledger = ledger
        self._last_probe: dict[str, float] = {}  # node_id -> last probe timestamp

    def should_probe(self, node: Node) -> bool:
        """判断节点是否需要发送探针。"""
        if not node.is_online:
            return False
        last = self._last_probe.get(node.node_id, 0)
        return time.time() - last >= PROBE_INTERVAL

    def create_probe_task(self, node: Node) -> Task:
        """为节点创建一个探针任务。

        探针任务内容：运行一个轻量 CPU benchmark。
        使用 busybox 镜像（极小，通用），执行简单计算。
        """
        task = Task(
            task_id=f"probe_{uuid.uuid4().hex[:8]}",
            user_id="system",
            image="busybox:latest",
            command=["sh", "-c", "echo 'PoA probe OK'; echo 'Computing...'; dd if=/dev/zero bs=1M count=10 2>/dev/null | md5sum"],
            requirement=Resources(cpu_cores_total=1, gpu_count_total=0, memory_mb_total=64),
            timeout_seconds=PROBE_TIMEOUT,
            task_type=TaskType.PROBE,
            preemptible=True,
        )
        task.assigned_node = node.node_id
        self._last_probe[node.node_id] = time.time()
        logger.info(f"Probe task created for node {node.name}: {task.task_id}")
        return task

    def record_probe_result(self, task_id: str, node_id: str, success: bool) -> None:
        """记录探针结果。"""
        self.ledger.record_probe(node_id, success)
        status = "success" if success else "failed"
        logger.info(f"Probe {task_id} for node {node_id}: {status}")

    def get_pending_probes(self, nodes: list[Node]) -> list[Task]:
        """获取需要发送的探针任务列表。"""
        probes = []
        for node in nodes:
            if self.should_probe(node):
                probes.append(self.create_probe_task(node))
        return probes
