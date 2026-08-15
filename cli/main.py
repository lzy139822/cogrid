"""Cogrid CLI 客户端。

命令：
    cogrid register --name my-node --coordinator http://localhost:8000
    cogrid submit --image busybox:latest --cpu 2 -- echo hello
    cogrid status --task <task_id>
    cogrid contribution --node <node_id>
    cogrid leaderboard
    cogrid pool
    cogrid nodes
    cogrid intensity --node <node_id> --level aggressive
"""

from __future__ import annotations

import os
import sys
import json
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

app = typer.Typer(
    name="cogrid",
    help="Cogrid — 多用户共享算力合作社 CLI",
    no_args_is_help=True,
)
console = Console()


def get_coordinator() -> str:
    """获取协调器地址。"""
    return os.environ.get("COGRID_COORDINATOR_URL", "http://localhost:8000/api/v1")


def api(method: str, path: str, **kwargs) -> dict:
    """调用协调器 API。"""
    url = f"{get_coordinator()}{path}"
    try:
        with httpx.Client(timeout=30) as client:
            resp = getattr(client, method)(url, **kwargs)
            if resp.status_code >= 400:
                console.print(f"[red]错误 {resp.status_code}[/red]: {resp.text}")
                raise typer.Exit(1)
            return resp.json()
    except httpx.ConnectError:
        console.print(f"[red]无法连接协调器[/red]: {get_coordinator()}")
        raise typer.Exit(1)


@app.command()
def register(
    name: str = typer.Option(..., "--name", "-n", help="节点名称"),
    coordinator: str = typer.Option(None, "--coordinator", "-c", help="协调器地址"),
):
    """注册节点。"""
    if coordinator:
        os.environ["COGRID_COORDINATOR_URL"] = coordinator.rstrip("/") + "/api/v1"
    result = api("post", "/nodes/register", json={
        "node_name": name,
        "cpu_cores": os.cpu_count() or 4,
        "gpu_count": 0,
        "memory_mb": 8192,
        "intensity": "balanced",
    })
    console.print(Panel(
        f"[green]注册成功[/green]\n"
        f"节点 ID: [cyan]{result['node_id']}[/cyan]\n"
        f"心跳间隔: {result['heartbeat_interval']}s\n\n"
        f"[dim]保存节点 ID，用于后续命令。[/dim]",
        title="节点注册"
    ))


@app.command()
def submit(
    image: str = typer.Option(..., "--image", "-i", help="Docker 镜像"),
    cpu: int = typer.Option(1, "--cpu", help="CPU 核数"),
    gpu: int = typer.Option(0, "--gpu", help="GPU 卡数"),
    memory: int = typer.Option(512, "--memory", "-m", help="内存 (MB)"),
    timeout: int = typer.Option(3600, "--timeout", "-t", help="超时 (秒)"),
    user: str = typer.Option("anonymous", "--user", "-u", help="用户 ID"),
    command: list[str] = typer.Argument(None, help="启动命令"),
):
    """提交计算任务。"""
    result = api("post", "/tasks/submit", json={
        "user_id": user,
        "image": image,
        "command": command or [],
        "cpu_cores": cpu,
        "gpu_count": gpu,
        "memory_mb": memory,
        "timeout_seconds": timeout,
    })
    console.print(Panel(
        f"任务 ID: [cyan]{result['task_id']}[/cyan]\n"
        f"状态: [yellow]{result['status']}[/yellow]",
        title="任务提交"
    ))


@app.command()
def status(
    task_id: str = typer.Option(..., "--task", "-t", help="任务 ID"),
):
    """查询任务状态。"""
    result = api("get", f"/tasks/{task_id}")
    table = Table(title="任务状态")
    table.add_column("字段", style="cyan")
    table.add_column("值")
    for k, v in result.items():
        table.add_row(str(k), str(v))
    console.print(table)


@app.command()
def tasks(
    status_filter: Optional[str] = typer.Option(None, "--status", "-s", help="按状态过滤"),
    limit: int = typer.Option(20, "--limit", "-l", help="显示数量"),
):
    """列出任务。"""
    params = {"limit": limit}
    if status_filter:
        params["status"] = status_filter
    result = api("get", "/tasks", params=params)
    table = Table(title="任务列表")
    table.add_column("任务 ID", style="cyan")
    table.add_column("状态")
    table.add_column("类型")
    table.add_column("节点")
    table.add_column("镜像")
    for t in result["tasks"]:
        table.add_row(
            t["task_id"], t["status"], t["task_type"],
            t["assigned_node"] or "-", t["image"][:30],
        )
    console.print(table)


@app.command()
def contribution(
    node_id: str = typer.Option(..., "--node", "-n", help="节点 ID"),
):
    """查询节点贡献。"""
    result = api("get", f"/contribution/{node_id}")
    table = Table(title="贡献报告")
    table.add_column("指标", style="cyan")
    table.add_column("值")
    for k, v in result.items():
        if k == "share_ratio":
            table.add_row(k, f"{v:.2%}")
        elif k == "probe_success_rate":
            table.add_row(k, f"{v:.2%}")
        else:
            table.add_row(k, str(v))
    console.print(table)


@app.command()
def leaderboard(
    limit: int = typer.Option(20, "--limit", "-l", help="显示数量"),
):
    """贡献排行榜。"""
    result = api("get", "/leaderboard", params={"limit": limit})
    table = Table(title="🏆 贡献排行榜")
    table.add_column("排名", style="yellow", justify="right")
    table.add_column("节点 ID", style="cyan")
    table.add_column("贡献分", justify="right")
    table.add_column("份额", justify="right")
    table.add_column("探针成功率", justify="right")
    table.add_column("在线时长", justify="right")
    for i, r in enumerate(result["leaderboard"], 1):
        hours = r["online_seconds"] / 3600
        table.add_row(
            str(i), r["node_id"],
            f"{r['total_credits']:.1f}",
            f"{r['share_ratio']:.2%}",
            f"{r['probe_success_rate']:.0%}",
            f"{hours:.1f}h",
        )
    console.print(table)


@app.command()
def pool():
    """算力池状态。"""
    result = api("get", "/pool/status")
    table = Table(title="算力池状态")
    table.add_column("指标", style="cyan")
    table.add_column("值")
    for k, v in result.items():
        if k == "artifacts":
            table.add_row(k, f"{len(v)} 个固存产物")
        else:
            table.add_row(k, str(v))
    console.print(table)


@app.command()
def nodes():
    """列出所有节点。"""
    result = api("get", "/nodes")
    table = Table(title="节点列表")
    table.add_column("节点 ID", style="cyan")
    table.add_column("名称")
    table.add_column("状态")
    table.add_column("强度")
    table.add_column("CPU", justify="right")
    table.add_column("GPU", justify="right")
    table.add_column("任务数", justify="right")
    table.add_column("贡献分", justify="right")
    table.add_column("份额", justify="right")
    table.add_column("探针率", justify="right")
    for n in result["nodes"]:
        status_color = "green" if n["status"] == "online" else "red"
        table.add_row(
            n["node_id"], n["name"],
            f"[{status_color}]{n['status']}[/{status_color}]",
            n["intensity"],
            str(n["available_cpu"]), str(n["available_gpu"]),
            str(n["running_tasks"]),
            f"{n['credits']:.1f}",
            f"{n['share_ratio']:.2%}",
            f"{n['probe_success_rate']:.0%}",
        )
    console.print(table)


@app.command()
def intensity(
    node_id: str = typer.Option(..., "--node", "-n", help="节点 ID"),
    level: str = typer.Option(..., "--level", "-l", help="强度档位: conservative/balanced/aggressive"),
):
    """设置节点强度档位。"""
    if level not in ("conservative", "balanced", "aggressive"):
        console.print("[red]无效档位[/red]，可选: conservative, balanced, aggressive")
        raise typer.Exit(1)
    result = api("post", f"/nodes/{node_id}/intensity", json={"intensity": level})
    console.print(f"[green]强度已设置[/green]: {result['node_id']} -> {result['intensity']}")


if __name__ == "__main__":
    app()
