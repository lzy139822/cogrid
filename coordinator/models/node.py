"""节点与资源模型。"""

from __future__ import annotations

import time
from enum import Enum
from dataclasses import dataclass, field


class NodeStatus(str, Enum):
    """节点状态。"""
    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"  # 正在执行任务，资源已满


class IntensityLevel(str, Enum):
    """奉献强度档位。

    - conservative: 仅空闲时贡献
    - balanced: 留 30% 资源余量
    - aggressive: 留 10% 资源余量
    """
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"

    @property
    def reserve_ratio(self) -> float:
        """该档位下为本地保留的资源比例。"""
        return {
            IntensityLevel.CONSERVATIVE: 0.5,
            IntensityLevel.BALANCED: 0.3,
            IntensityLevel.AGGRESSIVE: 0.1,
        }[self]


@dataclass
class Resources:
    """计算资源描述。

    *_total: 物理总量
    *_usage_percent: 当前使用率 (0-100)
    *_available: 扣除本地保留后可奉献的量
    """
    cpu_cores_total: int = 0
    gpu_count_total: int = 0
    memory_mb_total: int = 0
    cpu_usage_percent: float = 0.0
    gpu_usage_percent: float = 0.0
    memory_usage_percent: float = 0.0

    def available_cpu(self, intensity: IntensityLevel = IntensityLevel.BALANCED) -> int:
        """根据强度档位计算可奉献的 CPU 核数。"""
        idle_ratio = max(0, 1 - self.cpu_usage_percent / 100)
        reserve = intensity.reserve_ratio
        return max(0, int(self.cpu_cores_total * idle_ratio * (1 - reserve)))

    def available_gpu(self, intensity: IntensityLevel = IntensityLevel.BALANCED) -> int:
        """根据强度档位计算可奉献的 GPU 数。"""
        idle_ratio = max(0, 1 - self.gpu_usage_percent / 100)
        reserve = intensity.reserve_ratio
        return max(0, int(self.gpu_count_total * idle_ratio * (1 - reserve)))

    def available_memory(self, intensity: IntensityLevel = IntensityLevel.BALANCED) -> int:
        """根据强度档位计算可奉献的内存 (MB)。"""
        idle_ratio = max(0, 1 - self.memory_usage_percent / 100)
        reserve = intensity.reserve_ratio
        return max(0, int(self.memory_mb_total * idle_ratio * (1 - reserve)))


@dataclass
class Node:
    """注册到协调器的贡献者节点。"""
    node_id: str
    name: str
    resources: Resources = field(default_factory=Resources)
    intensity: IntensityLevel = IntensityLevel.BALANCED
    status: NodeStatus = NodeStatus.OFFLINE
    registered_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    # 当前正在执行的任务 ID 列表
    running_tasks: list[str] = field(default_factory=list)
    # 节点归属用户（贡献者）。空串表示未绑定，抢占回收时按此字段判断归属。
    owner_user_id: str = ""
    # 记录当前在该节点上运行的任务分别占用了哪个用户的份额
    # {task_id: user_id}：键是被分配的任务 ID，值是占用该节点份额的用户。
    # 抢占回收时据此判断“谁的弹性任务占了我的份额”。
    shares_allocated: dict[str, str] = field(default_factory=dict)

    @property
    def is_online(self) -> bool:
        """节点是否在线（60 秒内有心跳）。"""
        return time.time() - self.last_heartbeat < 60 and self.status != NodeStatus.OFFLINE

    def touch(self) -> None:
        """更新心跳时间。"""
        self.last_heartbeat = time.time()

    def available_resources(self) -> Resources:
        """返回当前可奉献的资源量。"""
        return Resources(
            cpu_cores_total=self.resources.available_cpu(self.intensity),
            gpu_count_total=self.resources.available_gpu(self.intensity),
            memory_mb_total=self.resources.available_memory(self.intensity),
        )
