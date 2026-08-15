"""Agent 入口。

使用 asyncio 主循环：
    注册节点 -> 心跳循环（上报资源 -> 领取任务 -> 执行 -> 报告结果）-> 优雅关闭

运行示例：
    python -m agent.main --coordinator http://localhost:8000/api/v1 \\
        --name my-node --intensity balanced

也可以通过环境变量配置协调器地址：
    export COGRID_COORDINATOR_URL=http://localhost:8000/api/v1
    python -m agent.main --name my-node

运行时强度档位切换：
    发送 SIGUSR1 或 SIGHUP 信号可在 conservative/balanced/aggressive 间循环切换，
    无需重启 Agent：
        kill -USR1 <pid>
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import sys
from dataclasses import dataclass
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from agent.executor import TaskExecutor
from agent.monitor import IntensityLevel, ResourceMonitor
from agent.reporter import (
    DEFAULT_HEARTBEAT_INTERVAL,
    TaskAssignment,
    heartbeat,
    register,
    report_result,
    set_intensity,
)

# ------------------------------------------------------------------
# 日志与全局对象
# ------------------------------------------------------------------

logger = logging.getLogger("cogrid.agent")
console = Console()

# 强度档位循环顺序（SIGUSR1 / SIGHUP 按此顺序切换）
_INTENSITY_CYCLE = [
    IntensityLevel.CONSERVATIVE,
    IntensityLevel.BALANCED,
    IntensityLevel.AGGRESSIVE,
]


def _setup_logging(verbose: bool = False) -> None:
    """配置日志，使用 RichHandler 美化终端输出。"""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%H:%M:%S]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )


# ------------------------------------------------------------------
# Agent 运行时状态
# ------------------------------------------------------------------


@dataclass
class AgentState:
    """Agent 运行时可变状态。

    intensity 和 shutdown_requested 会被信号处理器异步修改，
    主循环每次迭代时读取最新值。
    """

    node_id: str = ""
    intensity: IntensityLevel = IntensityLevel.BALANCED
    shutdown_requested: bool = False
    tasks_completed: int = 0
    tasks_failed: int = 0

    def request_shutdown(self) -> None:
        """请求优雅关闭。"""
        if not self.shutdown_requested:
            self.shutdown_requested = True
            logger.info("收到关闭信号，准备优雅退出...")

    def cycle_intensity(self) -> IntensityLevel:
        """切换到下一个强度档位。

        顺序：conservative -> balanced -> aggressive -> conservative ...
        """
        idx = _INTENSITY_CYCLE.index(self.intensity)
        self.intensity = _INTENSITY_CYCLE[(idx + 1) % len(_INTENSITY_CYCLE)]
        return self.intensity


# ------------------------------------------------------------------
# 核心逻辑
# ------------------------------------------------------------------


async def _execute_and_report(
    state: AgentState,
    executor: TaskExecutor,
    coordinator_url: str,
    task: TaskAssignment,
) -> None:
    """执行单个任务并上报结果。

    Docker / 子进程执行是阻塞操作，通过 asyncio.to_thread
    在线程池中运行，不阻塞事件循环。
    """
    try:
        result = await asyncio.to_thread(executor.execute_task, task)

        await report_result(
            coordinator_url=coordinator_url,
            task_id=result.task_id,
            success=result.success,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration=result.duration_seconds,
            artifact_path=result.artifact_path,
        )

        if result.success:
            state.tasks_completed += 1
        else:
            state.tasks_failed += 1

    except Exception as e:
        logger.error(
            "任务 %s 执行/上报失败: %s", task.task_id, e, exc_info=True
        )
        state.tasks_failed += 1

        # 尽力上报失败结果（best-effort，不阻塞主循环）
        try:
            await report_result(
                coordinator_url=coordinator_url,
                task_id=task.task_id,
                success=False,
                exit_code=-1,
                stdout="",
                stderr=f"Agent 内部错误: {e}",
                duration=0.0,
            )
        except Exception:
            pass


async def _heartbeat_cycle(
    state: AgentState,
    monitor: ResourceMonitor,
    executor: TaskExecutor,
    coordinator_url: str,
) -> None:
    """单次心跳周期：上报资源 -> 领取任务 -> 并发执行 -> 报告结果。

    异常会向上传播，由主循环统一处理（如 404 触发重新注册）。
    """
    # 1. 获取当前负载
    load = monitor.get_current_load()

    # 2. 心跳，上报资源并领取任务
    tasks = await heartbeat(
        coordinator_url=coordinator_url,
        node_id=state.node_id,
        resources=load,
        intensity=state.intensity,
    )

    # 3. 并发执行所有任务并上报结果
    if tasks:
        logger.info("开始执行 %d 个任务...", len(tasks))
        await asyncio.gather(
            *[
                _execute_and_report(state, executor, coordinator_url, task)
                for task in tasks
            ]
        )


async def _register_with_retry(
    coordinator_url: str,
    node_name: str,
    monitor: ResourceMonitor,
    intensity: IntensityLevel,
    max_retries: int = 3,
) -> str:
    """注册节点，带重试。

    协调器可能暂时不可达，重试几次后再放弃。
    """
    resources = monitor.detect_resources()

    for attempt in range(1, max_retries + 1):
        try:
            node_id = await register(
                coordinator_url=coordinator_url,
                node_name=node_name,
                resources=resources,
                intensity=intensity,
            )
            return node_id
        except Exception as e:
            if attempt < max_retries:
                logger.warning(
                    "注册失败（第 %d/%d 次）: %s，%ds 后重试...",
                    attempt,
                    max_retries,
                    e,
                    attempt * 2,
                )
                await asyncio.sleep(attempt * 2)
            else:
                raise


async def _run_agent(
    coordinator_url: str,
    node_name: str,
    intensity: IntensityLevel,
    heartbeat_interval: int = DEFAULT_HEARTBEAT_INTERVAL,
) -> None:
    """Agent 主循环。

    流程：
        1. 打印启动信息与物理资源
        2. 注册节点（带重试）
        3. 设置信号处理器
        4. 心跳主循环（每 heartbeat_interval 秒一轮）
        5. 收到关闭信号后优雅退出
    """
    state = AgentState(intensity=intensity)
    monitor = ResourceMonitor()
    executor = TaskExecutor()

    # ---- 打印启动信息 ----
    console.print()
    console.print("[bold cyan]Cogrid 节点 Agent[/bold cyan]", justify="center")
    console.print(f"  协调器:   {coordinator_url}")
    console.print(f"  节点名:   {node_name}")
    console.print(
        f"  强度档位: {intensity.value}（{intensity.label}，"
        f"保留 {intensity.reserve_ratio:.0%}）"
    )
    console.print()

    # ---- 注册节点 ----
    try:
        state.node_id = await _register_with_retry(
            coordinator_url=coordinator_url,
            node_name=node_name,
            monitor=monitor,
            intensity=intensity,
        )
    except Exception as e:
        console.print(f"[red]节点注册失败: {e}[/red]")
        console.print(
            "[yellow]请确认协调器已启动且地址正确。[/yellow]"
        )
        sys.exit(1)

    # ---- 打印资源信息 ----
    resources = monitor.detect_resources()
    table = Table(title="物理资源", show_header=True, header_style="bold")
    table.add_column("资源", style="cyan")
    table.add_column("总量", style="green")
    table.add_column("本地保留", style="yellow")
    table.add_row(
        "CPU",
        f"{resources.cpu_cores_total} 核",
        f"{intensity.reserve_ratio:.0%}",
    )
    table.add_row(
        "GPU",
        f"{resources.gpu_count_total} 卡",
        f"{intensity.reserve_ratio:.0%}",
    )
    table.add_row(
        "内存",
        f"{resources.memory_mb_total} MB",
        f"{intensity.reserve_ratio:.0%}",
    )
    console.print(table)
    console.print()
    console.print(
        f"[green]节点注册成功: {state.node_id}[/green]"
    )

    # ---- 设置信号处理器 ----
    loop = asyncio.get_running_loop()

    def _on_shutdown() -> None:
        state.request_shutdown()

    def _on_cycle_intensity() -> None:
        try:
            new_level = state.cycle_intensity()
            logger.info(
                "强度档位已切换为: %s（%s，保留 %.0f%%）",
                new_level.value,
                new_level.label,
                new_level.reserve_ratio * 100,
            )
            # 异步通知协调器（不阻塞信号处理器）
            asyncio.create_task(
                set_intensity(coordinator_url, state.node_id, new_level)
            )
        except Exception as e:
            logger.error("切换强度档位失败: %s", e, exc_info=True)

    # 注册 Unix 信号处理器
    signal_handlers = [
        (signal.SIGINT, _on_shutdown),
        (signal.SIGTERM, _on_shutdown),
        (signal.SIGUSR1, _on_cycle_intensity),
        (signal.SIGHUP, _on_cycle_intensity),
    ]
    registered_signals = []
    for sig, handler in signal_handlers:
        try:
            loop.add_signal_handler(sig, handler)
            registered_signals.append(sig)
        except (NotImplementedError, AttributeError, ValueError):
            # Windows 不支持 add_signal_handler / SIGUSR1 / SIGHUP
            pass

    # 提示运行时操作
    hints = [f"心跳间隔: {heartbeat_interval}s"]
    if signal.SIGUSR1 in registered_signals:
        hints.append("SIGUSR1/SIGHUP 切换强度档位")
    hints.append("Ctrl+C 优雅关闭")
    console.print(f"[dim]{' | '.join(hints)}[/dim]")
    console.print()

    # ---- 心跳主循环 ----
    while not state.shutdown_requested:
        try:
            await _heartbeat_cycle(
                state, monitor, executor, coordinator_url
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                # 节点未注册（协调器可能重启），尝试重新注册
                logger.warning(
                    "节点 %s 未在协调器注册（可能已重启），重新注册...",
                    state.node_id,
                )
                try:
                    state.node_id = await _register_with_retry(
                        coordinator_url=coordinator_url,
                        node_name=node_name,
                        monitor=monitor,
                        intensity=state.intensity,
                        max_retries=1,
                    )
                    logger.info("重新注册成功: %s", state.node_id)
                except Exception as re:
                    logger.error("重新注册失败: %s", re)
            else:
                logger.error(
                    "心跳 HTTP 错误: %d %s",
                    e.response.status_code,
                    e.response.text[:200],
                )
        except httpx.RequestError as e:
            # 网络错误（协调器不可达），等待下一轮重试
            logger.warning("心跳网络错误（协调器不可达）: %s", e)
        except Exception as e:
            logger.error("心跳周期异常: %s", e, exc_info=True)

        # 分段睡眠，以便快速响应关闭信号
        for _ in range(heartbeat_interval):
            if state.shutdown_requested:
                break
            await asyncio.sleep(1)

    # ---- 优雅关闭 ----
    console.print()
    console.print("[yellow]正在关闭 Agent...[/yellow]")
    executor.shutdown()

    # 打印执行统计
    stats = Table(title="执行统计", show_header=True, header_style="bold")
    stats.add_column("指标", style="cyan")
    stats.add_column("数值", style="green")
    stats.add_row("成功任务", str(state.tasks_completed))
    stats.add_row("失败任务", str(state.tasks_failed))
    stats.add_row("节点 ID", state.node_id)
    console.print(stats)
    console.print()
    console.print("[green]Agent 已关闭。[/green]")


# ------------------------------------------------------------------
# CLI 入口（typer）
# ------------------------------------------------------------------


def main(
    coordinator: Optional[str] = typer.Option(
        None,
        "--coordinator",
        "-c",
        envvar="COGRID_COORDINATOR_URL",
        help="协调器 REST API 基地址（默认 http://localhost:8000/api/v1）",
    ),
    name: Optional[str] = typer.Option(
        None,
        "--name",
        "-n",
        help="节点名称（默认使用主机名）",
    ),
    intensity: str = typer.Option(
        "balanced",
        "--intensity",
        "-i",
        help="强度档位: conservative / balanced / aggressive",
    ),
    heartbeat_interval: int = typer.Option(
        DEFAULT_HEARTBEAT_INTERVAL,
        "--heartbeat-interval",
        help="心跳间隔（秒），默认 15",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="启用 DEBUG 级别日志",
    ),
) -> None:
    """启动 Cogrid 节点 Agent，连接协调器并开始贡献算力。"""
    _setup_logging(verbose=verbose)

    # 解析协调器地址：CLI 参数 > 环境变量 > 默认值
    coordinator_url = coordinator or os.environ.get(
        "COGRID_COORDINATOR_URL", "http://localhost:8000/api/v1"
    )

    # 解析节点名：未指定时使用主机名
    node_name = name or socket.gethostname()

    # 解析强度档位
    try:
        intensity_level = IntensityLevel(intensity.lower().strip())
    except ValueError:
        console.print(f"[red]无效的强度档位: {intensity}[/red]")
        console.print(
            "[yellow]可选值: conservative, balanced, aggressive[/yellow]"
        )
        raise typer.Exit(1)

    # 启动 asyncio 主循环
    asyncio.run(
        _run_agent(
            coordinator_url=coordinator_url,
            node_name=node_name,
            intensity=intensity_level,
            heartbeat_interval=heartbeat_interval,
        )
    )


if __name__ == "__main__":
    typer.run(main)
