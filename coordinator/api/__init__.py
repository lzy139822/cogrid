"""REST API 路由。

提供两类接口：
- CLI/仪表盘调用：任务提交、状态查询、贡献查询、池状态
- Agent 调用：节点注册、心跳、任务领取、结果上报

点火阶段使用 HTTP 替代 gRPC（简化部署，后续可迁移到 gRPC，proto 已定义）。
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from coordinator.models.node import Node, NodeStatus, Resources, IntensityLevel
from coordinator.models.task import Task, TaskStatus, TaskType, TaskAssignment, TaskResult
from coordinator.scheduler import Scheduler
from coordinator.queue import TaskQueue
from coordinator.ledger import Ledger
from coordinator.prober import Prober
from coordinator.filler import Filler

logger = logging.getLogger(__name__)

router = APIRouter()

# ===== 请求/响应模型 =====


class RegisterRequest(BaseModel):
    node_name: str
    cpu_cores: int = 0
    gpu_count: int = 0
    memory_mb: int = 0
    intensity: str = "balanced"


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


class ReportResultRequest(BaseModel):
    task_id: str
    success: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    artifact_path: str = ""


# ===== 健康检查 =====


@router.get("/health")
async def health():
    return {"status": "ok", "service": "cogrid-coordinator"}


# ===== 节点管理（Agent 调用） =====


@router.post("/nodes/register")
async def register_node(req: RegisterRequest):
    """Agent 注册节点。"""
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
    )
    scheduler.register_node(node)
    ledger.get_or_create(node_id)
    logger.info(f"Node registered: {req.node_name} -> {node_id}")
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


# ===== 任务管理（CLI 调用） =====


@router.post("/tasks/submit")
async def submit_task(req: SubmitTaskRequest):
    """提交计算任务。"""
    queue: TaskQueue = router.state["queue"]
    scheduler: Scheduler = router.state["scheduler"]

    task = Task(
        user_id=req.user_id,
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

    logger.info(f"Task submitted: {task.task_id} by {req.user_id}")
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

    # 根据任务类型处理结果
    if task.task_type == TaskType.PROBE:
        prober.record_probe_result(req.task_id, task.assigned_node, req.success)
    elif task.task_type == TaskType.FILLER:
        if req.success and req.artifact_path:
            filler.record_artifact(req.task_id, req.artifact_path, task.artifact_desc)

    return {"acknowledged": True}


# ===== 贡献查询 =====


@router.get("/contribution/{node_id}")
async def get_contribution(node_id: str):
    """查询节点贡献。"""
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
            "available_cpu": node.available_resources().cpu_cores_total,
            "available_gpu": node.available_resources().gpu_count_total,
            "running_tasks": len(node.running_tasks),
            "credits": report["total_credits"],
            "share_ratio": report["share_ratio"],
            "probe_success_rate": report["probe_success_rate"],
        })
    return {"nodes": nodes}
