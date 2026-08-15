"""REST API 路由。

提供两类接口：
- CLI/仪表盘调用：任务提交、状态查询、贡献查询、池状态
- Agent 调用：节点注册、心跳、任务领取、结果上报

认证：
- POST /auth/register、POST /auth/login、GET /auth/me 为认证接口（无需 token）
- POST /tasks/submit、POST /nodes/register、GET /contribution/{node_id} 需要认证
- /health 及其他查询接口不强制认证
- 环境变量 COGRID_AUTH_DISABLED=1 时，无 Authorization header 的请求使用默认用户（兼容旧测试）

点火阶段使用 HTTP 替代 gRPC（简化部署，后续可迁移到 gRPC，proto 已定义）。
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel, Field

from coordinator.auth import UserManager
from coordinator.models.node import Node, NodeStatus, Resources, IntensityLevel
from coordinator.models.task import Task, TaskStatus, TaskType, TaskAssignment, TaskResult
from coordinator.scheduler import Scheduler
from coordinator.queue import TaskQueue
from coordinator.ledger import Ledger
from coordinator.prober import Prober
from coordinator.filler import Filler

logger = logging.getLogger(__name__)

router = APIRouter()

# 认证关闭时的默认用户 ID（兼容旧测试 / 无认证场景）
DEFAULT_USER_ID = "anonymous"

# ===== 请求/响应模型 =====


class RegisterRequest(BaseModel):
    node_name: str
    cpu_cores: int = 0
    gpu_count: int = 0
    memory_mb: int = 0
    intensity: str = "balanced"
    owner_user_id: str = ""  # 节点归属用户（贡献者），用于抢占回收鉴权


class HeartbeatRequest(BaseModel):
    node_id: str
    cpu_cores: int = 0
    gpu_count: int = 0
    memory_mb: int = 0
    cpu_usage_percent: float = 0.0
    gpu_usage_percent: float = 0.0
    memory_usage_percent: float = 0.0
    intensity: str = "balanced"


class SubmitTaskRequest(BaseModel):
    user_id: str = "anonymous"
    image: str
    command: list[str] = Field(default_factory=list)
    cpu_cores: int = 1
    gpu_count: int = 0
    memory_mb: int = 512
    timeout_seconds: int = 3600
    preemptible: bool = True


class SetIntensityRequest(BaseModel):
    intensity: str  # conservative / balanced / aggressive


class ReclaimRequest(BaseModel):
    """贡献者回收节点资源请求。"""
    owner_user_id: str         # 请求者用户 ID（须与节点 owner 一致）
    required_cpu: int = 0      # 需要的 CPU 核数
    required_gpu: int = 0      # 需要的 GPU 卡数
    required_memory: int = 0  # 需要的内存 (MB)


class ReportResultRequest(BaseModel):
    task_id: str
    success: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    artifact_path: str = ""
    checkpoint_data: str = ""  # 任务被抢占时保存的进度快照


class RegisterUserRequest(BaseModel):
    """用户注册请求。"""
    username: str
    password: str


class LoginRequest(BaseModel):
    """用户登录请求。"""
    username: str
    password: str


# ===== 认证依赖 =====


def get_current_user(authorization: Optional[str] = Header(None)) -> str:
    """从 Authorization header 提取并验证 Bearer token，返回 user_id。

    用于需要认证的路由（作为 FastAPI 依赖注入）。

    - 从 Authorization: Bearer <token> 中提取 token
    - 通过 UserManager.verify_token 验证，返回对应的 user_id
    - 无效或缺失时抛出 401

    兼容开关：如果请求未携带 Authorization header 且环境变量
    COGRID_AUTH_DISABLED=1，则返回默认用户 ID（"anonymous"），
    使旧测试和无认证场景仍能正常工作。
    """
    user_manager: UserManager = router.state.get("user_manager")

    # 认证关闭开关：无 header 且环境变量开启时用默认用户
    if authorization is None:
        if os.environ.get("COGRID_AUTH_DISABLED") == "1":
            return DEFAULT_USER_ID
        raise HTTPException(status_code=401, detail="未提供认证信息，请在 Authorization header 中携带 Bearer token")

    # 解析 Bearer token
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="认证格式错误，需要 'Bearer <token>'")
    token = authorization[len("Bearer "):]

    if user_manager is None:
        # UserManager 未初始化（防御性处理，正常流程不应发生）
        raise HTTPException(status_code=503, detail="认证服务不可用")

    user_id = user_manager.verify_token(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="无效或已过期的 token")
    return user_id


# ===== 健康检查 =====


@router.get("/health")
async def health():
    return {"status": "ok", "service": "cogrid-coordinator"}


# ===== 认证接口（无需 token） =====


@router.post("/auth/register")
async def auth_register(req: RegisterUserRequest):
    """用户注册。

    请求体：{username, password}
    返回：{user_id, token}
    """
    user_manager: UserManager = router.state["user_manager"]
    try:
        user_id, token = await user_manager.register(req.username, req.password)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    logger.info(f"用户注册: {req.username} -> {user_id}")
    return {"user_id": user_id, "token": token}


@router.post("/auth/login")
async def auth_login(req: LoginRequest):
    """用户登录。

    请求体：{username, password}
    返回：{user_id, token}
    """
    user_manager: UserManager = router.state["user_manager"]
    result = await user_manager.login(req.username, req.password)
    if result is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    user_id, token = result
    logger.info(f"用户登录: {req.username} -> {user_id}")
    return {"user_id": user_id, "token": token}


@router.get("/auth/me")
async def auth_me(user_id: str = Depends(get_current_user)):
    """查看当前认证用户信息（需 token）。"""
    user_manager: UserManager = router.state.get("user_manager")
    user = user_manager.get_user(user_id) if user_manager else None
    if user is None:
        # 认证关闭时的默认用户或 token 对应用户不在内存中
        return {"user_id": user_id, "username": user_id, "role": "anonymous"}
    return {
        "user_id": user.user_id,
        "username": user.username,
        "role": user.role.value,
        "created_at": user.created_at,
    }


# ===== 节点管理（Agent 调用） =====


@router.post("/nodes/register")
async def register_node(req: RegisterRequest, owner_user_id: str = Depends(get_current_user)):
    """Agent 注册节点（需认证，节点归属当前用户）。"""
    import uuid
    scheduler: Scheduler = router.state["scheduler"]
    ledger: Ledger = router.state["ledger"]

    node_id = f"node_{uuid.uuid4().hex[:8]}"
    node = Node(
        node_id=node_id,
        name=req.node_name,
        resources=Resources(
            cpu_cores_total=req.cpu_cores,
            gpu_count_total=req.gpu_count,
            memory_mb_total=req.memory_mb,
        ),
        intensity=IntensityLevel(req.intensity),
        status=NodeStatus.ONLINE,
        owner_user_id=owner_user_id,
    )
    scheduler.register_node(node, owner_user_id=owner_user_id)
    ledger.get_or_create(node_id)
    logger.info(f"Node registered: {req.node_name} -> {node_id} (owner={owner_user_id})")
    return {"node_id": node_id, "heartbeat_interval": 15}


@router.post("/nodes/heartbeat")
async def heartbeat(req: HeartbeatRequest):
    """Agent 心跳：上报资源、领取任务。"""
    scheduler: Scheduler = router.state["scheduler"]
    queue: TaskQueue = router.state["queue"]
    prober: Prober = router.state["prober"]
    filler: Filler = router.state["filler"]

    resources = Resources(
        cpu_cores_total=req.cpu_cores,
        gpu_count_total=req.gpu_count,
        memory_mb_total=req.memory_mb,
        cpu_usage_percent=req.cpu_usage_percent,
        gpu_usage_percent=req.gpu_usage_percent,
        memory_usage_percent=req.memory_usage_percent,
    )
    node = scheduler.update_node_heartbeat(req.node_id, resources, req.intensity)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found. Please register first.")

    # 尝试调度任务给这个节点
    assignments = []

    # 1. 检查是否需要发送探针
    if prober.should_probe(node):
        probe_task = prober.create_probe_task(node)
        queue.enqueue(probe_task)

    # 2. 检查是否需要生成填充任务（池空闲时）
    if filler.is_pool_idle():
        filler_tasks = filler.generate_filler_tasks()
        for ft in filler_tasks:
            queue.enqueue(ft)

    # 3. 调度任务
    # 先尝试抢占填充任务为用户任务腾位
    pending = queue.get_pending_count()
    if pending > 0:
        # 尝试调度多个任务（直到节点资源用完）
        for _ in range(3):
            result = scheduler.schedule_next()
            if result is None:
                break
            task, assigned_node = result
            if assigned_node.node_id != node.node_id:
                # 任务分配给了别的节点，通过心跳通知
                continue
            assignments.append({
                "task_id": task.task_id,
                "image": task.image,
                "command": task.command,
                "cpu_cores": task.requirement.cpu_cores_total,
                "gpu_count": task.requirement.gpu_count_total,
                "memory_mb": task.requirement.memory_mb_total,
                "timeout_seconds": task.timeout_seconds,
                "task_type": task.task_type.value,
                "preemptible": task.preemptible,
                "can_checkpoint": task.can_checkpoint,
                "checkpoint_data": task.checkpoint_data,
            })
            queue.update_status(task.task_id, TaskStatus.RUNNING, node.node_id)

    return {"acknowledged": True, "tasks": assignments}


@router.post("/nodes/{node_id}/intensity")
async def set_intensity(node_id: str, req: SetIntensityRequest):
    """设置节点强度档位。"""
    scheduler: Scheduler = router.state["scheduler"]
    node = scheduler.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    node.intensity = IntensityLevel(req.intensity)
    logger.info(f"Node {node_id} intensity set to {req.intensity}")
    return {"node_id": node_id, "intensity": req.intensity}


@router.post("/nodes/{node_id}/reclaim")
async def reclaim_node(node_id: str, req: ReclaimRequest):
    """贡献者回收自己的节点资源（抢占式回收）。

    当贡献者需要使用自己的算力份额时，抢占正在使用其份额的弹性任务。
    需要认证：请求者的 owner_user_id 必须与节点绑定的 owner_user_id 一致。

    参考：docs/specs/2026-08-15-cogrid-design.md §2.1 抢占回收
    """
    scheduler: Scheduler = router.state["scheduler"]
    node = scheduler.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")

    # 认证：验证请求者是节点归属用户
    if not node.owner_user_id:
        raise HTTPException(
            status_code=403,
            detail="Node has no owner bound. Set owner_user_id on registration first.",
        )
    if req.owner_user_id != node.owner_user_id:
        raise HTTPException(
            status_code=403,
            detail="Owner verification failed: requester does not own this node.",
        )

    # 触发抢占回收
    preempted = scheduler.preempt_for_owner(
        owner_user_id=req.owner_user_id,
        required_cpu=req.required_cpu,
        required_gpu=req.required_gpu,
        required_memory=req.required_memory,
    )

    # 计算释放的资源量（抢占后节点的可用资源）
    avail = node.available_resources()
    logger.info(
        f"Node {node_id} reclaimed by {req.owner_user_id}: "
        f"preempted {preempted} tasks"
    )
    return {
        "node_id": node_id,
        "owner_user_id": req.owner_user_id,
        "preempted_count": preempted,
        "freed_cpu_cores": avail.cpu_cores_total,
        "freed_gpu_count": avail.gpu_count_total,
        "freed_memory_mb": avail.memory_mb_total,
    }


# ===== 任务管理（CLI 调用） =====


@router.post("/tasks/submit")
async def submit_task(req: SubmitTaskRequest, user_id: str = Depends(get_current_user)):
    """提交计算任务（需认证，user_id 从 token 获取，不再从 body 取）。"""
    queue: TaskQueue = router.state["queue"]
    scheduler: Scheduler = router.state["scheduler"]

    task = Task(
        user_id=user_id,
        image=req.image,
        command=req.command,
        requirement=Resources(
            cpu_cores_total=req.cpu_cores,
            gpu_count_total=req.gpu_count,
            memory_mb_total=req.memory_mb,
        ),
        timeout_seconds=req.timeout_seconds,
        task_type=TaskType.USER_TASK,
        preemptible=req.preemptible,
    )
    queue.enqueue(task)

    # 尝试抢占填充任务为用户任务腾位
    scheduler.preempt_fillers_for_user_task()

    logger.info(f"Task submitted: {task.task_id} by {user_id}")
    return {"task_id": task.task_id, "status": "pending"}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """查询任务状态。"""
    queue: TaskQueue = router.state["queue"]
    task = queue.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "task_id": task.task_id,
        "status": task.status.value,
        "task_type": task.task_type.value,
        "assigned_node": task.assigned_node,
        "image": task.image,
        "user_id": task.user_id,
        "preemptible": task.preemptible,
        "can_checkpoint": task.can_checkpoint,
        "has_checkpoint": bool(task.checkpoint_data),
        "created_at": task.created_at,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
    }


@router.get("/tasks")
async def list_tasks(status: Optional[str] = None, limit: int = 50):
    """列出任务。"""
    queue: TaskQueue = router.state["queue"]
    tasks = queue.get_all_tasks()
    if status:
        tasks = [t for t in tasks if t.status.value == status]
    tasks = tasks[:limit]
    return {"tasks": [
        {
            "task_id": t.task_id,
            "status": t.status.value,
            "task_type": t.task_type.value,
            "assigned_node": t.assigned_node,
            "image": t.image,
        }
        for t in tasks
    ]}


# ===== Agent 结果上报 =====


@router.post("/tasks/report")
async def report_result(req: ReportResultRequest):
    """Agent 上报任务执行结果。"""
    queue: TaskQueue = router.state["queue"]
    scheduler: Scheduler = router.state["scheduler"]
    prober: Prober = router.state["prober"]
    filler: Filler = router.state["filler"]

    task = queue.get(req.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    scheduler.task_completed(req.task_id, req.success)

    # 如果 Agent 回传了 checkpoint 数据，保存到任务对象上
    if req.checkpoint_data:
        task.checkpoint_data = req.checkpoint_data
        task.can_checkpoint = True

    # 根据任务类型处理结果
    if task.task_type == TaskType.PROBE:
        prober.record_probe_result(req.task_id, task.assigned_node, req.success)
    elif task.task_type == TaskType.FILLER:
        if req.success and req.artifact_path:
            filler.record_artifact(req.task_id, req.artifact_path, task.artifact_desc)

    return {"acknowledged": True}


# ===== 贡献查询 =====


@router.get("/contribution/{node_id}")
async def get_contribution(node_id: str, user_id: str = Depends(get_current_user)):
    """查询节点贡献（需认证）。"""
    ledger: Ledger = router.state["ledger"]
    if node_id not in ledger.all_records():
        raise HTTPException(status_code=404, detail="Node not found")
    return ledger.get_report(node_id)


@router.get("/leaderboard")
async def get_leaderboard(limit: int = 20):
    """获取贡献排行榜。"""
    ledger: Ledger = router.state["ledger"]
    return {"leaderboard": ledger.get_leaderboard(limit)}


# ===== 池状态 =====


@router.get("/pool/status")
async def pool_status():
    """获取算力池状态。"""
    scheduler: Scheduler = router.state["scheduler"]
    filler: Filler = router.state["filler"]
    status = scheduler.get_pool_status()
    status["artifacts"] = filler.get_artifacts()
    return status


@router.get("/nodes")
async def list_nodes():
    """列出所有节点。"""
    scheduler: Scheduler = router.state["scheduler"]
    ledger: Ledger = router.state["ledger"]
    nodes = []
    for node in scheduler.get_all_nodes():
        report = ledger.get_report(node.node_id)
        nodes.append({
            "node_id": node.node_id,
            "name": node.name,
            "status": "online" if node.is_online else "offline",
            "intensity": node.intensity.value,
            "owner_user_id": node.owner_user_id,
            "available_cpu": node.available_resources().cpu_cores_total,
            "available_gpu": node.available_resources().gpu_count_total,
            "running_tasks": len(node.running_tasks),
            "credits": report["total_credits"],
            "share_ratio": report["share_ratio"],
            "probe_success_rate": report["probe_success_rate"],
        })
    return {"nodes": nodes}
