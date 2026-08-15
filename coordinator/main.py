"""Cogrid 协调器入口。

启动 FastAPI 服务，提供 REST API。
后台循环：节点超时检查、PoA 探针派发、算力固存。

运行：
    uvicorn coordinator.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from coordinator.api import router
from coordinator.filler import Filler
from coordinator.ledger import Ledger
from coordinator.prober import Prober
from coordinator.queue import TaskQueue
from coordinator.scheduler import Scheduler
from coordinator.storage import Storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cogrid.coordinator")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动后台任务。"""
    logger.info("Cogrid 协调器启动中...")

    # 初始化 SQLite 持久化存储（路径可通过环境变量覆盖）
    db_path = os.environ.get("COGRID_DB_PATH", "data/cogrid.db")
    storage = Storage(db_path)
    await storage.init()

    # 初始化核心组件，注入 Storage 实现持久化
    queue = TaskQueue(storage)
    ledger = Ledger(storage)
    scheduler = Scheduler(queue, ledger)
    prober = Prober(queue, ledger)
    filler = Filler(queue, scheduler)

    # 从存储恢复历史状态（贡献记录、未完成任务）
    try:
        await ledger.load_from_storage()
        await queue.load_from_storage()
    except Exception:
        # 恢复失败不应阻止启动，以空状态继续
        logger.exception("从存储恢复状态失败，将以空状态启动")

    # 注入到 API router
    router.state = {
        "queue": queue,
        "ledger": ledger,
        "scheduler": scheduler,
        "prober": prober,
        "filler": filler,
        "storage": storage,
    }
    app.state.scheduler = scheduler
    app.state.queue = queue
    app.state.ledger = ledger
    app.state.prober = prober
    app.state.filler = filler
    app.state.storage = storage

    # 启动后台循环
    bg_task = asyncio.create_task(background_loop(scheduler, queue, prober, filler))

    logger.info("Cogrid 协调器就绪 — 等待节点连接")
    yield

    # 关闭：取消后台循环并关闭存储
    bg_task.cancel()
    await storage.close()
    logger.info("Cogrid 协调器已关闭")


async def background_loop(
    scheduler: Scheduler,
    queue: TaskQueue,
    prober: Prober,
    filler: Filler,
) -> None:
    """后台循环：每 5 秒执行一次维护任务。

    - 检查节点超时
    - 生成并发送 PoA 探针
    - 检查池空闲并生成填充任务
    - 尝试调度待处理任务
    """
    while True:
        try:
            # 1. 检查节点超时
            offline = scheduler.check_node_timeout()
            if offline:
                logger.warning(f"Nodes went offline: {offline}")

            # 2. 为在线节点生成探针
            online_nodes = scheduler.get_online_nodes()
            for node in online_nodes:
                if prober.should_probe(node):
                    probe = prober.create_probe_task(node)
                    queue.enqueue(probe)
                    logger.info(f"PoA probe dispatched to {node.name}")

            # 3. 检查池空闲并生成填充任务
            if filler.is_pool_idle():
                filler_tasks = filler.generate_filler_tasks()
                for ft in filler_tasks:
                    queue.enqueue(ft)
                if filler_tasks:
                    logger.info(f"Dispatched {len(filler_tasks)} filler tasks (compute solidification)")

            # 4. 尝试调度待处理任务
            while True:
                result = scheduler.schedule_next()
                if result is None:
                    break
                task, node = result
                from coordinator.models.task import TaskStatus
                queue.update_status(task.task_id, TaskStatus.RUNNING, node.node_id)
                logger.info(f"Task {task.task_id} scheduled to {node.name}")

        except Exception as e:
            logger.error(f"Background loop error: {e}", exc_info=True)

        await asyncio.sleep(5)


# 创建 FastAPI 应用
app = FastAPI(
    title="Cogrid Coordinator",
    description="多用户共享算力合作社 — 协调器",
    version="0.1.0",
    lifespan=lifespan,
)

# 挂载 API 路由
app.include_router(router, prefix="/api/v1")


@app.get("/")
async def root():
    return {
        "service": "cogrid-coordinator",
        "version": "0.1.0",
        "docs": "/docs",
        "api": "/api/v1",
    }
