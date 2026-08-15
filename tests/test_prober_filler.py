"""PoA 探针与算力固存测试。"""

import time
import pytest
from coordinator.queue import TaskQueue
from coordinator.ledger import Ledger
from coordinator.scheduler import Scheduler
from coordinator.prober import Prober, PROBE_INTERVAL
from coordinator.filler import Filler
from coordinator.models.node import Node, Resources, IntensityLevel, NodeStatus
from coordinator.models.task import Task, TaskType, TaskStatus


def make_online_node(node_id: str) -> Node:
    return Node(
        node_id=node_id, name=f"test-{node_id}",
        resources=Resources(cpu_cores_total=8, gpu_count_total=0, memory_mb_total=16384),
        intensity=IntensityLevel.AGGRESSIVE,
        status=NodeStatus.ONLINE,
    )


# ===== PoA 探针测试 =====


def test_probe_creation():
    """测试探针任务创建。"""
    queue = TaskQueue()
    ledger = Ledger()
    prober = Prober(queue, ledger)
    node = make_online_node("node_1")

    assert prober.should_probe(node)  # 从未探针过

    task = prober.create_probe_task(node)
    assert task.task_type == TaskType.PROBE
    assert task.assigned_node == "node_1"
    assert task.preemptible is True
    assert not prober.should_probe(node)  # 刚探针过


def test_probe_result_recording():
    """测试探针结果记录。"""
    queue = TaskQueue()
    ledger = Ledger()
    prober = Prober(queue, ledger)

    prober.record_probe_result("probe_1", "node_1", True)
    prober.record_probe_result("probe_2", "node_1", False)
    rec = ledger.get_or_create("node_1")
    assert rec.probe_total_count == 2
    assert rec.probe_success_count == 1


def test_probe_interval():
    """测试探针间隔。"""
    queue = TaskQueue()
    ledger = Ledger()
    prober = Prober(queue, ledger)
    node = make_online_node("node_1")

    # 第一次应该探针
    assert prober.should_probe(node)
    prober.create_probe_task(node)

    # 刚探针过，不应该再探针
    assert not prober.should_probe(node)

    # 模拟时间流逝
    prober._last_probe["node_1"] = time.time() - PROBE_INTERVAL - 1
    assert prober.should_probe(node)


# ===== 算力固存测试 =====


def test_filler_idle_detection():
    """测试池空闲检测。"""
    queue = TaskQueue()
    ledger = Ledger()
    scheduler = Scheduler(queue, ledger)
    filler = Filler(queue, scheduler)

    # 无在线节点，不算空闲
    assert not filler.is_pool_idle()

    # 有在线节点，无任务 -> 空闲
    node = make_online_node("node_1")
    scheduler.register_node(node)
    assert filler.is_pool_idle()

    # 有用户任务 -> 不空闲
    task = Task(task_id="user_1", task_type=TaskType.USER_TASK)
    queue.enqueue(task)
    assert not filler.is_pool_idle()


def test_filler_task_generation():
    """测试填充任务生成。"""
    queue = TaskQueue()
    ledger = Ledger()
    scheduler = Scheduler(queue, ledger)
    filler = Filler(queue, scheduler)

    node = make_online_node("node_1")
    scheduler.register_node(node)

    tasks = filler.generate_filler_tasks()
    assert len(tasks) == 1
    assert tasks[0].task_type == TaskType.FILLER
    assert tasks[0].preemptible is True


def test_filler_artifact_recording():
    """测试固存产物记录。"""
    queue = TaskQueue()
    ledger = Ledger()
    scheduler = Scheduler(queue, ledger)
    filler = Filler(queue, scheduler)

    filler.record_artifact("filler_1", "/data/cache/result.txt", "pre-computed cache entry")
    artifacts = filler.get_artifacts()
    assert len(artifacts) == 1
    assert artifacts[0]["task_id"] == "filler_1"
    assert artifacts[0]["artifact_path"] == "/data/cache/result.txt"
