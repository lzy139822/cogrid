"""本地负载监测与强度档位控制。

使用 psutil 获取 CPU / 内存使用率，通过 nvidia-smi 检测 GPU。
强度档位决定为本地保留多少资源余量：
    - conservative（保守）: 保留 50%
    - balanced（均衡）:     保留 30%
    - aggressive（激进）:   保留 10%
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import psutil

logger = logging.getLogger("cogrid.agent.monitor")


class IntensityLevel(str, Enum):
    """奉献强度档位。

    通过 reserve_ratio 属性获取为本地保留的资源比例。
    """

    CONSERVATIVE = "conservative"  # 保守：保留 50%
    BALANCED = "balanced"          # 均衡：保留 30%
    AGGRESSIVE = "aggressive"      # 激进：保留 10%

    @property
    def reserve_ratio(self) -> float:
        """该档位下为本地保留的资源比例 (0.0 - 1.0)。"""
        return {
            IntensityLevel.CONSERVATIVE: 0.5,
            IntensityLevel.BALANCED: 0.3,
            IntensityLevel.AGGRESSIVE: 0.1,
        }[self]

    @property
    def label(self) -> str:
        """中文标签，用于日志和展示。"""
        return {
            IntensityLevel.CONSERVATIVE: "保守",
            IntensityLevel.BALANCED: "均衡",
            IntensityLevel.AGGRESSIVE: "激进",
        }[self]


@dataclass
class SystemResources:
    """系统资源快照。

    字段含义：
        *_total:         物理总量
        *_usage_percent: 当前使用率 (0 - 100)
    """

    cpu_cores_total: int = 0
    gpu_count_total: int = 0
    memory_mb_total: int = 0
    cpu_usage_percent: float = 0.0
    gpu_usage_percent: float = 0.0
    memory_usage_percent: float = 0.0


class ResourceMonitor:
    """本地资源监测器。

    负责检测物理资源总量、获取当前负载、根据强度档位计算可奉献资源。
    GPU 检测结果会缓存，避免重复调用 nvidia-smi。
    """

    def __init__(self) -> None:
        self._gpu_count: Optional[int] = None  # GPU 数量缓存

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def detect_resources(self) -> SystemResources:
        """检测物理资源总量（启动时调用一次）。

        Returns:
            包含 CPU 核数、GPU 卡数、内存总量的 SystemResources。
        """
        cpu_cores = (
            psutil.cpu_count(logical=False)
            or psutil.cpu_count(logical=True)
            or 1
        )
        memory_mb = int(psutil.virtual_memory().total / (1024 * 1024))
        gpu_count = self._detect_gpu_count()

        logger.info(
            "检测到物理资源: CPU=%d 核, GPU=%d 卡, 内存=%d MB",
            cpu_cores,
            gpu_count,
            memory_mb,
        )
        return SystemResources(
            cpu_cores_total=cpu_cores,
            gpu_count_total=gpu_count,
            memory_mb_total=memory_mb,
        )

    def get_current_load(self) -> SystemResources:
        """获取当前负载快照（含使用率）。

        每次心跳时调用，返回物理总量与当前使用率。
        """
        # CPU 使用率：首次调用 cpu_percent(interval=None) 返回 0.0，
        # 需做一次短采样获取有意义的数据
        cpu_usage = psutil.cpu_percent(interval=None)
        if cpu_usage == 0.0:
            cpu_usage = psutil.cpu_percent(interval=0.5)

        mem = psutil.virtual_memory()
        memory_mb = int(mem.total / (1024 * 1024))

        gpu_usage = self._get_gpu_usage()

        cpu_cores = (
            psutil.cpu_count(logical=False)
            or psutil.cpu_count(logical=True)
            or 1
        )

        return SystemResources(
            cpu_cores_total=cpu_cores,
            gpu_count_total=self._gpu_count or 0,
            memory_mb_total=memory_mb,
            cpu_usage_percent=round(cpu_usage, 1),
            gpu_usage_percent=round(gpu_usage, 1),
            memory_usage_percent=round(mem.percent, 1),
        )

    def available_resources(
        self, load: SystemResources, intensity: IntensityLevel
    ) -> SystemResources:
        """根据当前负载和强度档位计算可奉献的资源量。

        可奉献量 = 物理总量 x 空闲比例 x (1 - 保留比例)

        Args:
            load:       get_current_load() 返回的负载快照
            intensity:  当前强度档位

        Returns:
            包含可奉献 CPU / GPU / 内存的 SystemResources。
        """
        reserve = intensity.reserve_ratio

        idle_cpu = max(0.0, 1 - load.cpu_usage_percent / 100)
        idle_gpu = max(0.0, 1 - load.gpu_usage_percent / 100)
        idle_mem = max(0.0, 1 - load.memory_usage_percent / 100)

        return SystemResources(
            cpu_cores_total=max(
                0, int(load.cpu_cores_total * idle_cpu * (1 - reserve))
            ),
            gpu_count_total=max(
                0, int(load.gpu_count_total * idle_gpu * (1 - reserve))
            ),
            memory_mb_total=max(
                0, int(load.memory_mb_total * idle_mem * (1 - reserve))
            ),
            # 使用率原样保留，供调用方参考
            cpu_usage_percent=load.cpu_usage_percent,
            gpu_usage_percent=load.gpu_usage_percent,
            memory_usage_percent=load.memory_usage_percent,
        )

    # ------------------------------------------------------------------
    # GPU 检测（内部方法）
    # ------------------------------------------------------------------

    def _detect_gpu_count(self) -> int:
        """检测 NVIDIA GPU 数量（通过 nvidia-smi）。

        结果会被缓存。没有 GPU 或 nvidia-smi 不可用时返回 0。
        """
        if self._gpu_count is not None:
            return self._gpu_count

        if shutil.which("nvidia-smi") is None:
            self._gpu_count = 0
            return 0

        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name",
                    "--format=csv,noheader",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                count = len(
                    [
                        line
                        for line in result.stdout.strip().splitlines()
                        if line.strip()
                    ]
                )
                self._gpu_count = count
                if count > 0:
                    logger.info("检测到 %d 张 NVIDIA GPU", count)
                return count
        except Exception as e:
            logger.warning("GPU 检测失败: %s", e)

        self._gpu_count = 0
        return 0

    def _get_gpu_usage(self) -> float:
        """获取 GPU 平均使用率 (0 - 100)。

        没有 GPU 或读取失败时返回 0.0。
        """
        if self._gpu_count is None or self._gpu_count == 0:
            return 0.0

        if shutil.which("nvidia-smi") is None:
            return 0.0

        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                lines = [
                    float(line.strip())
                    for line in result.stdout.strip().splitlines()
                    if line.strip()
                ]
                if lines:
                    return round(sum(lines) / len(lines), 1)
        except Exception:
            pass

        return 0.0
