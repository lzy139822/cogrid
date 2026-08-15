"""抢占式调度测试。"""

import pytest
import time
from coordinator.queue import TaskQueue
from coordinator.scheduler import Scheduler
from coordinator.ledger import Ledger
from coordinator.checkpoint import CheckpointManager
from coordinator.models.node import Node, Resources, IntensityLevel, NodeStatus
from coordinator.models.task import Task, TaskStatus, TaskType


def make_node(node_id: str, owner: str = "", cpu: int = 8) -> Node:
    return Node(
        node_id=node_id, name=f"test-{node_id}",
        resources=Resources(cpu_cores_total=cpu, gpu_count_total=0, memory_mb_total=16384),
        intensity=IntensityLevel.AGGRESSIVE,
        status=NodeStatus.ONLINE,
        owner_user_id=owner,
    )


def make_user_task(task_id: str, user_id: str = "user1", cpu: int = 2, preemptible: bool = True) -> Task:
    return Task(
        task_id=task_id, user_id=user_id,
        image="busybox:latest", command=["echo", "hello"],
        requirement=Resources(cpu_cores_total=cpu, memory_mb_total=256),
        task_type=TaskType.USER_TASK, preemptible=preemptible,
    )


def make_filler_task(task_id: str, node_id: str = "") -> Task:
    return Task(
        task_id=task_id, user_id="system",
        image="busybox:latest", command=["echo", "filler"],
        requirement=Resources(cpu_cores_total=2, memory_mb_total=256),
        task_type=TaskType.FILLER, preemptible=True,
        assigned_node=node_id,
    )


# ===== 队列辅助方法测试 =====


def test_queue_get_running_tasks():
    """测试获取运行中任务。"""
    q = TaskQueue()
    t1 = make_user_task("t1")
    t2 = make_user_task("t2")
    q.enqueue(t1)
    q.enqueue(t2)
    q.update_status("t1", TaskStatus.RUNNING, "node1")
    running = q.get_running_tasks()
    assert len(running) == 1
    assert running[0].task_id == "t1"


def test_queue_get_tasks_by_user():
    """测试按用户过滤任务。"""
    q = TaskQueue()
    q.enqueue(make_user_task("t1", "alice"))
    q.enqueue(make_user_task("t2", "bob"))
    q.enqueue(make_user_task("t3", "alice"))

    alice_tasks = q.get_tasks_by_user("alice")
    assert len(alice_tasks) == 2
    bob_tasks = q.get_tasks_by_user("bob")
    assert len(bob_tasks) == 1


def test_queue_requeue():
    """测试任务重新入队。"""
    q = TaskQueue()
    t = make_user_task("t1")
    q.enqueue(t)
    q.update_status("t1", TaskStatus.RUNNING, "node1")

    assert q.requeue("t1")
    task = q.get("t1")
    assert task.status == TaskStatus.PENDING
    assert task.assigned_node == ""


# ===== 抢占调度测试 =====


def test_preempt_for_owner():
    """测试贡献者回收份额。"""
    queue = TaskQueue()
    ledger = Ledger()
    scheduler = Scheduler(queue, ledger)

    # owner 的节点
    node = make_node("node1", owner="alice", cpu=4)
    scheduler.register_node(node, owner_user_id="alice")

    # bob 的弹性任务在 alice 的节点上跑
    bob_task = make_user_task("bob_task", user_id="bob", preemptible=True)
    queue.enqueue(bob_task)
    queue.update_status("bob_task", TaskStatus.RUNNING, "node1")
    node.running_tasks.append("bob_task")
    node.shares_allocated["bob_task"] = "bob"

    # alice 回收
    count = scheduler.preempt_for_owner("alice", required_cpu=4, required_gpu=0, required_memory=0)
    assert count == 1

    # bob 的任务被抢占，重新入队
    task = queue.get("bob_task")
    assert task.status == TaskStatus.PENDING
    assert "bob_task" not in node.running_tasks


def test_preempt_for_owner_no_other_users():
    """测试 owner 回收时没有其他用户任务可抢占。"""
    queue = TaskQueue()
    ledger = Ledger()
    scheduler = Scheduler(queue, ledger)

    node = make_node("node1", owner="alice", cpu=4)
    scheduler.register_node(node, owner_user_id="alice")

    # alice 自己的任务
    alice_task = make_user_task("alice_task", user_id="alice")
    queue.enqueue(alice_task)
    queue.update_status("alice_task", TaskStatus.RUNNING, "node1")
    node.running_tasks.append("alice_task")
    node.shares_allocated["alice_task"] = "alice"

    # 不应抢占自己的任务
    count = scheduler.preempt_for_owner("alice", required_cpu=4, required_gpu=0, required_memory=0)
    assert count == 0


def test_preempt_fillers_for_user_task():
    """测试为用户任务抢占填充任务。"""
    queue = TaskQueue()
    ledger = Ledger()
    scheduler = Scheduler(queue, ledger)

    node = make_node("node1", owner="alice", cpu=4)
    scheduler.register_node(node, owner_user_id="alice")

    # 填充任务在跑
    filler = make_filler_task("filler1", "node1")
    queue.enqueue(filler)
    queue.update_status("filler1", TaskStatus.RUNNING, "node1")
    node.running_tasks.append("filler1")

    # 用户任务排队
    user_task = make_user_task("user_task1", user_id="bob")
    queue.enqueue(user_task)

    # 自动抢占
    count = scheduler.check_and_preempt()
    assert count >= 1

    # 填充任务被抢占
    filler_task = queue.get("filler1")
    assert filler_task.status == TaskStatus.PENDING


def test_check_and_preempt_no_user_tasks():
    """没有用户任务排队时不触发抢占。"""
    queue = TaskQueue()
    ledger = Ledger()
    scheduler = Scheduler(queue, ledger)

    node = make_node("node1", owner="alice", cpu=4)
    scheduler.register_node(node, owner_user_id="alice")

    # 只有填充任务
    filler = make_filler_task("filler1", "node1")
    queue.enqueue(filler)
    queue.update_status("filler1", TaskStatus.RUNNING, "node1")
    node.running_tasks.append("filler1")

    count = scheduler.check_and_preempt()
    assert count == 0  # 没有用户任务，不抢占


# ===== Checkpoint 测试 =====


def test_checkpoint_save_load():
    """测试 checkpoint 保存和加载。"""
    queue = TaskQueue()
    task = make_user_task("task1")
    queue.enqueue(task)
    mgr = CheckpointManager(queue)
    mgr.save_checkpoint("task1", "progress:50%")

    data = mgr.load_checkpoint("task1")
    assert data == "progress:50%"


def test_checkpoint_clear():
    """测试 checkpoint 清除。"""
    queue = TaskQueue()
    task = make_user_task("task1")
    queue.enqueue(task)
    mgr = CheckpointManager(queue)
    mgr.save_checkpoint("task1", "progress:50%")
    mgr.clear_checkpoint("task1")

    assert mgr.load_checkpoint("task1") is None


def test_checkpoint_not_found():
    """测试不存在的 checkpoint。"""
    queue = TaskQueue()
    mgr = CheckpointManager(queue)
    assert mgr.load_checkpoint("nonexistent") is None


# ===== 节点归属测试 =====


def test_node_owner_field():
    """测试节点 owner 字段。"""
    node = make_node("node1", owner="alice")
    assert node.owner_user_id == "alice"
    assert node.shares_allocated == {}


def test_scheduler_register_with_owner():
    """测试带 owner 的节点注册。"""
    queue = TaskQueue()
    ledger = Ledger()
    scheduler = Scheduler(queue, ledger)
    node = make_node("node1", owner="alice")
    scheduler.register_node(node, owner_user_id="alice")
    assert scheduler.get_node("node1").owner_user_id == "alice"


def test_pool_status_by_owner():
    """测试池状态按 owner 分组。"""
    queue = TaskQueue()
    ledger = Ledger()
    scheduler = Scheduler(queue, ledger)

    scheduler.register_node(make_node("node1", owner="alice", cpu=8), owner_user_id="alice")
    scheduler.register_node(make_node("node2", owner="bob", cpu=4), owner_user_id="bob")

    status = scheduler.get_pool_status()
    assert "by_owner" in status
    assert "alice" in status["by_owner"]
    assert "bob" in status["by_owner"]
