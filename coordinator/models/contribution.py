"""贡献值记录模型。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ContributionRecord:
    """节点的贡献值记录。

    贡献分 = 资源量 × 在线时长 × 探针成功率 × 质量系数

    - total_credits: 累积总贡献分（只增不减）
    - probe_success_count / probe_total_count: PoA 探针成功/总数
    - quality_factor: 质量系数，奖励稳定在线、任务成功率高的节点
    - online_seconds: 累积在线时长（秒）
    - last_credit_time: 上次结算贡献分的时间戳
    """
    node_id: str
    total_credits: float = 0.0
    probe_success_count: int = 0
    probe_total_count: int = 0
    quality_factor: float = 1.0
    online_seconds: float = 0.0
    last_credit_time: float = field(default_factory=time.time)

    @property
    def probe_success_rate(self) -> float:
        """探针成功率 (0.0 - 1.0)。"""
        if self.probe_total_count == 0:
            return 1.0  # 还没探针过，默认满分
        return self.probe_success_count / self.probe_total_count

    @property
    def share_ratio(self) -> float:
        """份额比例占位（实际由 Ledger 计算全网比例）。"""
        return 0.0  # 由 Ledger.get_share_ratio() 计算

    def record_probe(self, success: bool) -> None:
        """记录一次探针结果。"""
        self.probe_total_count += 1
        if success:
            self.probe_success_count += 1

    def add_credits(self, amount: float) -> None:
        """累加贡献分。"""
        self.total_credits += max(0, amount)

    def accumulate_online(self, seconds: float) -> None:
        """累加在线时长。"""
        self.online_seconds += max(0, seconds)
