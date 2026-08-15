"""SQLite 持久化层与 Ledger/TaskQueue 持久化集成测试。

覆盖两类核心约束：
1. 不传 Storage 时退化为纯内存模式，行为与原先一致且不报错。
2. 传入 Storage 后，写操作异步落盘，重启后 load_from_storage 能恢复状态。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from coordinator.ledger import Ledger
from coordinator.queue import TaskQueue
from coordinator.storage import Storage
from coordinator.models.node import Node, Resources, IntensityLevel, NodeStatus
from coordinator.models.task import Task, TaskStatus, TaskType


# ===== 辅助函数 =====


def make_node(node_id: str = "n1", cpu: int = 8) -> Node:
    """创建在线测试节点。"""
    return Node(
        node_id=node_id,
        name=f"node-{node_id}",
        resources=Resources(cpu_cores_total=cpu, gpu_count_total=0, memory_mb_total=16384),
        intensity=IntensityLevel.AGGRESSIVE,
        status=NodeStatus.ONLINE,
    )


def make_task(task_id: str, task_type: TaskType = TaskType.USER_TASK,
              preemptible: bool = True) -> Task:
    """创建测试任务。"""
    return Task(
        task_id=task_id,
        user_id="u1",
        image="busybox:latest",
        command=["echo", "hello"],
        requirement=Resources(cpu_cores_total=2, gpu_count_total=0, memory_mb_total=256),
        task_type=task_type,
        preemptible=preemptible,
    )


# ===== 纯内存模式（不传 Storage） =====


def test_ledger_pure_memory_mode():
    """不传 Storage 时 Ledger 为纯内存模式，_persist 不报错。"""
    ledger = Ledger()
    ledger.record_probe("n1", True)
    ledger._persist("n1")  # 无 storage / 无事件循环时应安全跳过
    assert ledger.get_credits("n1") == 0.0
    assert ledger.get_or_create("n1").probe_total_count == 1


def test_queue_pure_memory_mode():
    """不传 Storage 时 TaskQueue 为纯内存模式。"""
    q = TaskQueue()
    t = make_task("t1")
    q.enqueue(t)
    q._persist(t)  # 安全跳过
    assert q.get("t1") is not None
    assert q.get_pending_count() == 1


def test_ledger_backward_compat_no_arg():
    """Ledger() 无参构造（现有测试用法）依然可用。"""
    ledger = Ledger()
    rec = ledger.get_or_create("n1")
    assert rec.total_credits == 0.0
    assert rec.probe_success_rate == 1.0


def test_queue_backward_compat_no_arg():
    """TaskQueue() 无参构造（现有测试用法）依然可用。"""
    q = TaskQueue()
    assert q.dequeue() is None
    assert q.get_pending_count() == 0


# ===== Storage 建表与 CRUD =====


@pytest.mark.asyncio
async def test_storage_init_creates_tables(tmp_path):
    """init() 创建所有表，重复 init 幂等。"""
    db = str(tmp_path / "cogrid.db")
    storage = Storage(db)
    await storage.init()
    # 重复初始化不应报错（IF NOT EXISTS）
    await storage._create_tables()
    await storage._db.commit()
    await storage.close()
    assert (tmp_path / "cogrid.db").exists()


@pytest.mark.asyncio
async def test_storage_creates_parent_dir(tmp_path):
    """数据库路径父目录不存在时自动创建。"""
    db = str(tmp_path / "deep" / "nested" / "cogrid.db")
    storage = Storage(db)
    await storage.init()
    assert (tmp_path / "deep" / "nested" / "cogrid.db").exists()
    await storage.close()


@pytest.mark.asyncio
async def test_storage_node_artifact_crud(tmp_path):
    """节点与产物的 upsert / load。"""
    storage = Storage(str(tmp_path / "c.db"))
    await storage.init()

    node = make_node("n1")
    await storage.save_node(node)
    nodes = await storage.load_nodes()
    assert len(nodes) == 1
    assert nodes[0]["node_id"] == "n1"
    assert nodes[0]["cpu_cores"] == 8

    await storage.save_artifact("t1", "/data/x.txt", "cache")
    arts = await storage.load_artifacts()
    assert len(arts) == 1
    assert arts[0]["artifact_path"] == "/data/x.txt"
    await storage.close()


@pytest.mark.asyncio
async def test_storage_update_task_status_partial(tmp_path):
    """update_task_status 局部更新只改传入字段。"""
    storage = Storage(str(tmp_path / "c.db"))
    await storage.init()
    task = make_task("t1")
    await storage.save_task(task)
    await storage.update_task_status("t1", "running", node_id="n1", started_at=123.0)
    tasks = await storage.load_tasks()
    assert tasks[0]["status"] == "running"
    assert tasks[0]["assigned_node"] == "n1"
    assert tasks[0]["started_at"] == 123.0
    # 未传的字段保持不变
    assert tasks[0]["image"] == "busybox:latest"
    await storage.close()


# ===== Ledger 持久化往返 =====


@pytest.mark.asyncio
async def test_ledger_persistence_roundtrip(tmp_path):
    """Ledger 写操作落盘，重启后 load_from_storage 恢复贡献分。"""
    db = str(tmp_path / "cogrid.db")

    # 会话 1：写入
    storage = Storage(db)
    await storage.init()
    ledger = Ledger(storage)
    ledger.record_probe("n1", True)
    ledger.record_probe("n1", False)
    ledger._last_settle["n1"] = time.time() - 10
    node = make_node("n1")
    credits = ledger.settle_online_credits(node)
    assert credits > 0
    rec = ledger.get_or_create("n1")
    expected = (rec.total_credits, rec.probe_success_count, rec.probe_total_count, rec.online_seconds)
    # 等待 fire-and-forget 持久化完成
    await asyncio.sleep(0.1)
    await storage.close()

    # 会话 2：恢复
    storage2 = Storage(db)
    await storage2.init()
    ledger2 = Ledger(storage2)
    await ledger2.load_from_storage()
    rec2 = ledger2.get_or_create("n1")
    assert rec2.total_credits == pytest.approx(expected[0])
    assert rec2.probe_success_count == expected[1]
    assert rec2.probe_total_count == expected[2]
    assert rec2.online_seconds == pytest.approx(expected[3])
    # 份额比例保持（唯一节点 = 1.0）
    assert ledger2.get_share_ratio("n1") == pytest.approx(1.0)
    await storage2.close()


@pytest.mark.asyncio
async def test_ledger_load_from_storage_noop_without_storage():
    """未注入 Storage 时 load_from_storage 为空操作。"""
    ledger = Ledger()
    await ledger.load_from_storage()
    assert ledger.all_records() == {}


# ===== TaskQueue 持久化往返 =====


@pytest.mark.asyncio
async def test_queue_persistence_roundtrip(tmp_path):
    """TaskQueue 写操作落盘，重启后恢复未完成任务。"""
    db = str(tmp_path / "cogrid.db")

    storage = Storage(db)
    await storage.init()
    q = TaskQueue(storage)

    t_pending = make_task("t_pending")
    t_running = make_task("t_running", task_type=TaskType.FILLER)
    t_done = make_task("t_done", task_type=TaskType.PROBE)

    q.enqueue(t_pending)
    q.enqueue(t_running)
    q.update_status("t_running", TaskStatus.RUNNING, "n1")
    q.enqueue(t_done)
    q.update_status("t_done", TaskStatus.COMPLETED, "n1")

    await asyncio.sleep(0.1)
    await storage.close()

    # 重启恢复
    storage2 = Storage(db)
    await storage2.init()
    q2 = TaskQueue(storage2)
    await q2.load_from_storage()

    all_tasks = {t.task_id: t for t in q2.get_all_tasks()}
    # 未完成任务保留
    assert "t_pending" in all_tasks
    assert all_tasks["t_pending"].status == TaskStatus.PENDING
    assert all_tasks["t_pending"].command == ["echo", "hello"]
    # running 被重置为 pending 重新调度
    assert "t_running" in all_tasks
    assert all_tasks["t_running"].status == TaskStatus.PENDING
    assert all_tasks["t_running"].assigned_node == ""
    # 终结态任务不恢复
    assert "t_done" not in all_tasks
    # 恢复的任务可正常出队
    assert q2.dequeue() is not None
    await storage2.close()


@pytest.mark.asyncio
async def test_queue_load_from_storage_noop_without_storage():
    """未注入 Storage 时 load_from_storage 为空操作。"""
    q = TaskQueue()
    await q.load_from_storage()
    assert q.get_all_tasks() == []
