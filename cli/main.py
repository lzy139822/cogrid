"""Cogrid CLI 客户端。

命令：
    cogrid login --username alice --password alice123
    cogrid register-user --username alice --password alice123
    cogrid me
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
from pathlib import Path
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

# Token 持久化路径
TOKEN_FILE = Path.home() / ".cogrid" / "token"
CONFIG_FILE = Path.home() / ".cogrid" / "config.json"


def get_coordinator() -> str:
    """获取协调器地址。"""
    return os.environ.get("COGRID_COORDINATOR_URL", "http://localhost:8000/api/v1")


def save_token(token: str, user_id: str = "") -> None:
    """保存 token 到本地文件。"""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {"token": token, "user_id": user_id}
    TOKEN_FILE.write_text(json.dumps(data))
    # 文件权限 600（仅所有者可读写）
    TOKEN_FILE.chmod(0o600)


def load_token() -> str:
    """从本地文件加载 token。"""
    if TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text())
            return data.get("token", "")
        except Exception:
            pass
    return ""


def load_user_id() -> str:
    """从本地文件加载 user_id。"""
    if TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text())
            return data.get("user_id", "")
        except Exception:
            pass
    return ""


def clear_token() -> None:
    """清除本地 token。"""
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def api(method: str, path: str, auth: bool = True, **kwargs) -> dict:
    """调用协调器 API。

    Args:
        method: HTTP 方法 (get/post/put/delete)
        path:   API 路径（不含 base URL）
        auth:   是否自动携带认证 token（默认 True）
    """
    url = f"{get_coordinator()}{path}"
    headers = kwargs.pop("headers", {})

    if auth:
        token = load_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"

    try:
        with httpx.Client(timeout=30) as client:
            resp = getattr(client, method)(url, headers=headers, **kwargs)
            if resp.status_code == 401:
                console.print("[red]认证失败[/red]：请先运行 `cogrid login` 登录")
                raise typer.Exit(1)
            if resp.status_code >= 400:
                console.print(f"[red]错误 {resp.status_code}[/red]: {resp.text}")
                raise typer.Exit(1)
            return resp.json()
    except httpx.ConnectError:
        console.print(f"[red]无法连接协调器[/red]: {get_coordinator()}")
        raise typer.Exit(1)


@app.command(name="register-user")
def register_user(
    username: str = typer.Option(..., "--username", "-u", help="用户名"),
    password: str = typer.Option(..., "--password", "-p", help="密码"),
):
    """注册新用户。"""
    result = api("post", "/auth/register", auth=False, json={
        "username": username,
        "password": password,
    })
    save_token(result["token"], result["user_id"])
    console.print(Panel(
        f"[green]注册成功[/green]\n"
        f"用户 ID: [cyan]{result['user_id']}[/cyan]\n"
        f"Token 已保存到 [dim]{TOKEN_FILE}[/dim]",
        title="用户注册"
    ))


@app.command(name="login")
def login(
    username: str = typer.Option(..., "--username", "-u", help="用户名"),
    password: str = typer.Option(..., "--password", "-p", help="密码"),
):
    """用户登录。"""
    result = api("post", "/auth/login", auth=False, json={
        "username": username,
        "password": password,
    })
    save_token(result["token"], result["user_id"])
    console.print(Panel(
        f"[green]登录成功[/green]\n"
        f"用户 ID: [cyan]{result['user_id']}[/cyan]\n"
        f"Token 已保存到 [dim]{TOKEN_FILE}[/dim]",
        title="用户登录"
    ))


@app.command(name="logout")
def logout():
    """退出登录（清除本地 token）。"""
    clear_token()
    console.print("[green]已退出登录[/green]")


@app.command(name="me")
def me():
    """查看当前登录用户信息。"""
    result = api("get", "/auth/me")
    table = Table(title="当前用户")
    table.add_column("字段", style="cyan")
    table.add_column("值")
    for k, v in result.items():
        table.add_row(str(k), str(v))
    console.print(table)


@app.command()
def register(
    name: str = typer.Option(..., "--name", "-n", help="节点名称"),
    coordinator: str = typer.Option(None, "--coordinator", "-c", help="协调器地址"),
    intensity: str = typer.Option("balanced", "--intensity", "-i", help="强度档位"),
):
    """注册节点（需先登录，节点自动归属当前用户）。"""
    if coordinator:
        os.environ["COGRID_COORDINATOR_URL"] = coordinator.rstrip("/") + "/api/v1"
    result = api("post", "/nodes/register", json={
        "node_name": name,
        "cpu_cores": os.cpu_count() or 4,
        "gpu_count": 0,
        "memory_mb": 8192,
        "intensity": intensity,
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
    preemptible: bool = typer.Option(True, "--preemptible/--non-preemptible", help="是否可被抢占"),
    command: list[str] = typer.Argument(None, help="启动命令"),
):
    """提交计算任务（需先登录）。"""
    result = api("post", "/tasks/submit", json={
        "image": image,
        "command": command or [],
        "cpu_cores": cpu,
        "gpu_count": gpu,
        "memory_mb": memory,
        "timeout_seconds": timeout,
        "preemptible": preemptible,
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


@app.command()
def reclaim(
    node_id: str = typer.Option(..., "--node", "-n", help="要回收的节点 ID"),
    cpu: int = typer.Option(0, "--cpu", help="需要的 CPU 核数"),
    gpu: int = typer.Option(0, "--gpu", help="需要的 GPU 卡数"),
    memory: int = typer.Option(0, "--memory", "-m", help="需要的内存 (MB)"),
):
    """回收自己节点上的弹性任务（抢占式回收）。

    贡献者需要使用自己的算力时，抢占正在使用其份额的其他用户任务。
    需要先登录，且只能回收自己注册的节点。
    """
    user_id = load_user_id()
    if not user_id:
        console.print("[red]请先登录[/red]：cogrid login --username <user> --password <pass>")
        raise typer.Exit(1)

    result = api("post", f"/nodes/{node_id}/reclaim", json={
        "owner_user_id": user_id,
        "required_cpu": cpu,
        "required_gpu": gpu,
        "required_memory": memory,
    })
    console.print(Panel(
        f"[green]回收完成[/green]\n"
        f"节点: [cyan]{result['node_id']}[/cyan]\n"
        f"抢占任务数: [yellow]{result['preempted_count']}[/yellow]\n"
        f"释放 CPU: {result['freed_cpu_cores']} 核\n"
        f"释放 GPU: {result['freed_gpu_count']} 卡\n"
        f"释放内存: {result['freed_memory_mb']} MB",
        title="资源回收"
    ))


if __name__ == "__main__":
    app()
