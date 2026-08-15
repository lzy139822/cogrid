"""贡献值账本。

负责：
- 记录每个节点的贡献分累积
- 计算 PoA 探针成功率
- 计算全网份额比例（按比例分红的核心）
- 结算在线时长的贡献分

贡献分公式：
    贡献分 = 资源量(CPU核 + GPU卡×10 + 内存GB/2) × 在线时长(秒) × 探针成功率 × 质量系数

TODO(ledger): 考虑加入消费扣减机制，当前只有贡献累积。
下一步：在 consume() 方法中实现消费记录，影响实时占用额度但不影响贡献分。
参考：docs/specs/2026-08-15-cogrid-design.md §2.1
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Dict, Optional

from coordinator.models.contribution import ContributionRecord
from coordinator.models.node import Node

if TYPE_CHECKING:
    from coordinator.storage import Storage

logger = logging.getLogger(__name__)


class Ledger:
    """贡献值账本 — 算力合作社的"银行"。

    线程安全：所有方法通过 asyncio.Lock 保护（在 async 上下文中使用）。
    对于点火阶段，使用简单内存存储 + SQLite 持久化。

    持久化：构造函数接收可选的 Storage 实例。传入后，所有写操作
    （record_probe、settle_online_credits 等）会异步保存到 SQLite；
    不传入时退化为纯内存模式，确保单元测试与轻量场景不受影响。
    """

    def __init__(self, storage: "Optional[Storage]" = None) -> None:
        self._records: Dict[str, ContributionRecord] = {}
        self._last_settle: Dict[str, float] = {}  # node_id -> last settle timestamp
        self._storage = storage

    def _persist(self, node_id: str) -> None:
        """将单个节点的贡献记录异步写入 SQLite（fire-and-forget）。

        - 未注入 Storage 时直接返回（纯内存模式）。
        - 在没有运行中事件循环时（如同步单元测试）安全跳过。
        """
        if self._storage is None:
            return
        rec = self._records.get(node_id)
        if rec is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 没有运行中的事件循环，跳过异步持久化
            return
        task = loop.create_task(self._storage.save_contribution(rec))

        def _on_done(t: "asyncio.Future") -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.warning(f"持久化贡献记录失败 (node={node_id}): {exc}")

        task.add_done_callback(_on_done)

    def get_or_create(self, node_id: str) -> ContributionRecord:
        """获取或创建节点的贡献记录。"""
        if node_id not in self._records:
            self._records[node_id] = ContributionRecord(node_id=node_id)
            self._last_settle[node_id] = time.time()
        return self._records[node_id]

    def record_probe(self, node_id: str, success: bool) -> None:
        """记录一次 PoA 探针结果。"""
        rec = self.get_or_create(node_id)
        rec.record_probe(success)
        logger.debug(f"Probe recorded for {node_id}: success={success}, rate={rec.probe_success_rate:.2%}")
        self._persist(node_id)

    def settle_online_credits(self, node: Node) -> float:
        """结算节点在线期间累积的贡献分。

        在每次心跳时调用，计算自上次结算以来的贡献分。
        返回本次结算的贡献分量。
        """
        if not node.is_online:
            return 0.0

        rec = self.get_or_create(node.node_id)
        now = time.time()
        last = self._last_settle.get(node.node_id, now)
        elapsed = now - last
        if elapsed <= 0:
            return 0.0

        # 资源量加权：CPU 1分/核，GPU 10分/卡，内存 0.5分/GB
        avail = node.available_resources()
        resource_weight = (
            avail.cpu_cores_total * 1.0
            + avail.gpu_count_total * 10.0
            + avail.memory_mb_total / 1024 * 0.5
        )

        credits = resource_weight * elapsed * rec.probe_success_rate * rec.quality_factor
        rec.add_credits(credits)
        rec.accumulate_online(elapsed)
        rec.last_credit_time = now
        self._last_settle[node.node_id] = now

        logger.debug(
            f"Settled {credits:.2f} credits for {node.node_id} "
            f"(resource={resource_weight:.1f}, elapsed={elapsed:.0f}s, "
            f"probe_rate={rec.probe_success_rate:.2%}, quality={rec.quality_factor:.2f})"
        )
        # add_credits / accumulate_online 已修改记录，异步持久化
        self._persist(node.node_id)
        return credits

    def get_credits(self, node_id: str) -> float:
        """获取节点总贡献分。"""
        return self.get_or_create(node_id).total_credits

    def get_share_ratio(self, node_id: str) -> float:
        """计算节点的份额比例 = 自己贡献分 / 全网总贡献分。"""
        total = self.total_credits
        if total <= 0:
            return 0.0
        return self.get_or_create(node_id).total_credits / total

    def get_all_shares(self) -> Dict[str, float]:
        """获取所有节点的份额比例。"""
        total = self.total_credits
        if total <= 0:
            return {nid: 0.0 for nid in self._records}
        return {nid: rec.total_credits / total for nid, rec in self._records.items()}

    @property
    def total_credits(self) -> float:
        """全网总贡献分。"""
        return sum(r.total_credits for r in self._records.values())

    def get_report(self, node_id: str) -> dict:
        """获取节点的贡献报告（用于 API 响应）。"""
        rec = self.get_or_create(node_id)
        return {
            "node_id": node_id,
            "total_credits": round(rec.total_credits, 2),
            "share_ratio": round(self.get_share_ratio(node_id), 4),
            "probe_success_rate": round(rec.probe_success_rate, 4),
            "probe_success_count": rec.probe_success_count,
            "probe_total_count": rec.probe_total_count,
            "quality_factor": round(rec.quality_factor, 2),
            "online_seconds": round(rec.online_seconds, 0),
        }

    def get_leaderboard(self, limit: int = 20) -> list[dict]:
        """获取贡献排行榜。"""
        sorted_records = sorted(
            self._records.items(), key=lambda x: x[1].total_credits, reverse=True
        )[:limit]
        return [self.get_report(nid) for nid, _ in sorted_records]

    def all_records(self) -> Dict[str, ContributionRecord]:
        """返回所有记录（用于持久化）。"""
        return dict(self._records)

    async def load_from_storage(self) -> None:
        """启动时从 SQLite 加载历史贡献记录，恢复内存状态。

        - 未注入 Storage 时为空操作。
        - 加载后会重置 _last_settle 为当前时间，避免重启后一次性
          结算过长的"离线时长"导致贡献分异常膨胀。
        """
        if self._storage is None:
            return
        records = await self._storage.load_contributions()
        now = time.time()
        for r in records:
            node_id = r["node_id"]
            rec = ContributionRecord(
                node_id=node_id,
                total_credits=r["total_credits"],
                probe_success_count=r["probe_success_count"],
                probe_total_count=r["probe_total_count"],
                quality_factor=r["quality_factor"],
                online_seconds=r["online_seconds"],
                last_credit_time=r["last_credit_time"],
            )
            self._records[node_id] = rec
            # 重置结算基线为当前时间，避免重启后补结过长时间段
            self._last_settle[node_id] = now
        logger.info(f"从存储加载了 {len(records)} 条贡献记录")
