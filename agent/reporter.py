"""资源上报与心跳。

通过 HTTP (httpx) 与协调器通信。所有函数均为 async，
在 asyncio 主循环中调用。

API 端点约定（基地址 = coordinator_url，如 http://localhost:8000/api/v1）：
    POST /nodes/register              -> {node_id, heartbeat_interval}
    POST /nodes/heartbeat             -> {acknowledged, tasks: [...]}
    POST /tasks/report                -> {acknowledged}
    POST /nodes/{node_id}/intensity   -> {node_id, intensity}
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

from agent.monitor import IntensityLevel, SystemResources

logger = logging.getLogger("cogrid.agent.reporter")

# HTTP 请求超时（秒）
HTTP_TIMEOUT = 10.0

# 默认心跳间隔（秒），与协调器返回值一致
DEFAULT_HEARTBEAT_INTERVAL = 15

# 上报时 stdout / stderr 的最大字符数，避免请求体过大
MAX_OUTPUT_CHARS = 10000


@dataclass
class TaskAssignment:
    """协调器分配给本节点的任务。

    由 heartbeat() 返回，传给 executor.execute_task() 执行。
    """

    task_id: str
    image: str = ""
    command: list[str] = field(default_factory=list)
    cpu_cores: int = 1
    gpu_count: int = 0
    memory_mb: int = 512
    timeout_seconds: int = 3600
    task_type: str = "user_task"  # user_task / probe / filler
    preemptible: bool = True
    # 是否支持 checkpoint（抢占时可保存进度）
    can_checkpoint: bool = False
    # 已有的 checkpoint 数据（重新调度时用于续跑）
    checkpoint_data: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "TaskAssignment":
        """从协调器返回的 JSON 字典构造 TaskAssignment。"""
        return cls(
            task_id=data["task_id"],
            image=data.get("image", ""),
            command=list(data.get("command", [])),
            cpu_cores=data.get("cpu_cores", 1),
            gpu_count=data.get("gpu_count", 0),
            memory_mb=data.get("memory_mb", 512),
            timeout_seconds=data.get("timeout_seconds", 3600),
            task_type=data.get("task_type", "user_task"),
            preemptible=data.get("preemptible", True),
            can_checkpoint=data.get("can_checkpoint", False),
            checkpoint_data=data.get("checkpoint_data", ""),
        )


def _normalize_url(coordinator_url: str) -> str:
    """规范化协调器 URL，去除尾部斜杠。"""
    return coordinator_url.rstrip("/")


def _auth_headers(token: str = "") -> dict:
    """构造认证请求头。

    Args:
        token: Bearer token。为空时返回空字典（兼容无认证场景）。

    Returns:
        包含 Authorization header 的字典，或空字典。
    """
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


async def login(
    coordinator_url: str,
    username: str,
    password: str,
) -> tuple[str, str]:
    """用户登录，获取 user_id 和 token。

    Args:
        coordinator_url: 协调器 REST API 基地址
        username:        用户名
        password:        密码

    Returns:
        (user_id, token) 元组

    Raises:
        httpx.HTTPStatusError: 登录失败（401 用户名/密码错误）
        httpx.RequestError:    网络错误
    """
    base = _normalize_url(coordinator_url)
    payload = {"username": username, "password": password}

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(f"{base}/auth/login", json=payload)
        resp.raise_for_status()
        data = resp.json()

    user_id = data["user_id"]
    token = data["token"]
    logger.info("用户登录成功: %s -> %s", username, user_id)
    return (user_id, token)


async def register_user(
    coordinator_url: str,
    username: str,
    password: str,
) -> tuple[str, str]:
    """用户注册，获取 user_id 和 token。

    Args:
        coordinator_url: 协调器 REST API 基地址
        username:        用户名
        password:        密码

    Returns:
        (user_id, token) 元组

    Raises:
        httpx.HTTPStatusError: 注册失败（409 用户名已存在）
        httpx.RequestError:    网络错误
    """
    base = _normalize_url(coordinator_url)
    payload = {"username": username, "password": password}

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(f"{base}/auth/register", json=payload)
        resp.raise_for_status()
        data = resp.json()

    user_id = data["user_id"]
    token = data["token"]
    logger.info("用户注册成功: %s -> %s", username, user_id)
    return (user_id, token)


async def register(
    coordinator_url: str,
    node_name: str,
    resources: Optional[SystemResources] = None,
    intensity: IntensityLevel = IntensityLevel.BALANCED,
    owner_user_id: str = "",
    token: str = "",
) -> str:
    """注册节点到协调器。

    Args:
        coordinator_url: 协调器 REST API 基地址
        node_name:       节点名称
        resources:       物理资源总量；为 None 时自动检测
        intensity:       初始强度档位
        owner_user_id:   节点归属用户 ID（用于抢占回收鉴权）
        token:           认证 token（Bearer）。认证后节点自动归属该用户。

    Returns:
        协调器分配的 node_id

    Raises:
        httpx.HTTPStatusError: 注册失败（协调器返回非 2xx）
        httpx.RequestError:    网络错误（协调器不可达）
    """
    if resources is None:
        from agent.monitor import ResourceMonitor

        resources = ResourceMonitor().detect_resources()

    base = _normalize_url(coordinator_url)
    payload = {
        "node_name": node_name,
        "cpu_cores": resources.cpu_cores_total,
        "gpu_count": resources.gpu_count_total,
        "memory_mb": resources.memory_mb_total,
        "intensity": intensity.value,
        "owner_user_id": owner_user_id,
    }

    headers = _auth_headers(token)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(f"{base}/nodes/register", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    node_id = data["node_id"]
    interval = data.get("heartbeat_interval", DEFAULT_HEARTBEAT_INTERVAL)
    logger.info(
        "节点注册成功: %s -> %s（心跳间隔 %ss）",
        node_name,
        node_id,
        interval,
    )
    return node_id


async def heartbeat(
    coordinator_url: str,
    node_id: str,
    resources: SystemResources,
    intensity: IntensityLevel,
    token: str = "",
) -> list[TaskAssignment]:
    """发送心跳，上报资源并领取任务。

    Args:
        coordinator_url: 协调器 REST API 基地址
        node_id:         节点 ID
        resources:       当前负载快照（含物理总量和使用率）
        intensity:       当前强度档位
        token:           认证 token（可选，心跳接口不强制认证）

    Returns:
        协调器分配给本节点的任务列表（可能为空）

    Raises:
        httpx.HTTPStatusError: 心跳失败（如 404 表示节点未注册）
        httpx.RequestError:    网络错误
    """
    base = _normalize_url(coordinator_url)
    payload = {
        "node_id": node_id,
        "cpu_cores": resources.cpu_cores_total,
        "gpu_count": resources.gpu_count_total,
        "memory_mb": resources.memory_mb_total,
        "cpu_usage_percent": resources.cpu_usage_percent,
        "gpu_usage_percent": resources.gpu_usage_percent,
        "memory_usage_percent": resources.memory_usage_percent,
        "intensity": intensity.value,
    }

    headers = _auth_headers(token)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(f"{base}/nodes/heartbeat", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    raw_tasks = data.get("tasks", [])
    tasks = [TaskAssignment.from_dict(t) for t in raw_tasks]
    if tasks:
        logger.info("心跳成功，领取到 %d 个任务", len(tasks))
    return tasks


async def report_result(
    coordinator_url: str,
    task_id: str,
    success: bool,
    exit_code: int,
    stdout: str,
    stderr: str,
    duration: float,
    artifact_path: str = "",
    checkpoint_data: str = "",
    token: str = "",
) -> None:
    """上报任务执行结果。

    Args:
        coordinator_url: 协调器 REST API 基地址
        task_id:         任务 ID
        success:         是否成功
        exit_code:       退出码
        stdout:          标准输出
        stderr:          标准错误
        duration:        执行时长（秒）
        artifact_path:   产物路径（填充任务）
        checkpoint_data: 任务被抢占时保存的进度快照，用于续跑
        token:           认证 token（可选）
    """
    base = _normalize_url(coordinator_url)
    payload = {
        "task_id": task_id,
        "success": success,
        "exit_code": exit_code,
        "stdout": stdout[:MAX_OUTPUT_CHARS],
        "stderr": stderr[:MAX_OUTPUT_CHARS],
        "duration_seconds": round(duration, 3),
        "artifact_path": artifact_path,
        "checkpoint_data": checkpoint_data[:MAX_OUTPUT_CHARS],
    }

    headers = _auth_headers(token)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(f"{base}/tasks/report", json=payload, headers=headers)
        resp.raise_for_status()

    logger.info(
        "任务结果已上报: %s success=%s exit_code=%d 耗时=%.1fs",
        task_id,
        success,
        exit_code,
        duration,
    )


async def set_intensity(
    coordinator_url: str,
    node_id: str,
    intensity: IntensityLevel,
    token: str = "",
) -> None:
    """远程设置节点强度档位。

    Args:
        coordinator_url: 协调器 REST API 基地址
        node_id:         节点 ID
        intensity:       目标强度档位
        token:           认证 token（可选）
    """
    base = _normalize_url(coordinator_url)
    payload = {"intensity": intensity.value}

    headers = _auth_headers(token)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(
            f"{base}/nodes/{node_id}/intensity", json=payload, headers=headers
        )
        resp.raise_for_status()

    logger.info("强度档位已更新: %s（%s）", intensity.value, intensity.label)
