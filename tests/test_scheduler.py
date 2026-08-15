"""调度器与任务队列测试。"""

import pytest
from coordinator.queue import TaskQueue
from coordinator.scheduler import Scheduler
from coordinator.ledger import Ledger
from coordinator.models.node import Node, Resources, IntensityLevel, NodeStatus
from coordinator.models.task import Task, TaskStatus, TaskType


def make_node(node_id: str, cpu: int = 8, gpu: int = 0, mem: int = 16384) -> Node:
    """创建测试节点。"""
    return Node(
        node_id=node_id,
        name=f"test-{node_id}",
        resources=Resources(cpu_cores_total=cpu, gpu_count_total=gpu, memory_mb_total=mem),
        intensity=IntensityLevel.AGGRESSIVE,  # 激进模式，更多可用资源
        status=NodeStatus.ONLINE,
    )


def make_user_task(task_id: str = "", cpu: int = 2, preemptible: bool = True) -> Task:
    """创建测试用户任务。"""
    return Task(
        task_id=task_id or f"task_{id(object())}",
        user_id="user_1",
        image="busybox:latest",
        command=["echo", "hello"],
        requirement=Resources(cpu_cores_total=cpu, gpu_count_total=0, memory_mb_total=256),
        task_type=TaskType.USER_TASK,
        preemptible=preemptible,
    )


# ===== 队列测试 =====


def test_queue_enqueue_dequeue():
    """测试入队出队。"""
    q = TaskQueue()
    t1 = make_user_task("t1")
    t2 = make_user_task("t2")
    q.enqueue(t1)
    q.enqueue(t2)
    assert q.get_pending_count() == 2
    out = q.dequeue()
    assert out is not None
    assert out.task_id == "t1"


def test_queue_priority():
    """测试优先级：不可抢占 > 可抢占 > 探针 > 填充。"""
    q = TaskQueue()
    filler = Task(task_id="filler", task_type=TaskType.FILLER)
    probe = Task(task_id="probe", task_type=TaskType.PROBE)
    user_preempt = Task(task_id="user_p", task_type=TaskType.USER_TASK, preemptible=True)
    user_nonpreempt = Task(task_id="user_np", task_type=TaskType.USER_TASK, preemptible=False)

    # 逆序入队
    q.enqueue(filler)
    q.enqueue(probe)
    q.enqueue(user_preempt)
    q.enqueue(user_nonpreempt)

    # 应按优先级出队
    assert q.dequeue().task_id == "user_np"
    assert q.dequeue().task_id == "user_p"
    assert q.dequeue().task_id == "probe"
    assert q.dequeue().task_id == "filler"


def test_queue_get_task():
    """测试按 ID 查找。"""
    q = TaskQueue()
    t = make_user_task("test_task")
    q.enqueue(t)
    found = q.get("test_task")
    assert found is not None
    assert found.task_id == "test_task"
    assert q.get("nonexistent") is None


def test_queue_update_status():
    """测试状态更新。"""
    q = TaskQueue()
    t = make_user_task("test_task")
    q.enqueue(t)
    assert q.update_status("test_task", TaskStatus.RUNNING, "node_1")
    task = q.get("test_task")
    assert task.status == TaskStatus.RUNNING
    assert task.assigned_node == "node_1"
    assert task.started_at > 0


# ===== 调度器测试 =====


def test_scheduler_register_node():
    """测试节点注册。"""
    queue = TaskQueue()
    ledger = Ledger()
    scheduler = Scheduler(queue, ledger)
    node = make_node("node_1")
    scheduler.register_node(node)
    assert scheduler.get_node("node_1") is not None
    assert len(scheduler.get_online_nodes()) == 1


def test_scheduler_schedule_task():
    """测试任务调度。"""
    queue = TaskQueue()
    ledger = Ledger()
    scheduler = Scheduler(queue, ledger)
    node = make_node("node_1", cpu=8)
    scheduler.register_node(node)

    task = make_user_task("task_1", cpu=2)
    queue.enqueue(task)

    result = scheduler.schedule_next()
    assert result is not None
    task, assigned_node = result
    assert assigned_node.node_id == "node_1"
    assert task.task_id == "task_1"


def test_scheduler_no_resources():
    """测试资源不足时不调度。"""
    queue = TaskQueue()
    ledger = Ledger()
    scheduler = Scheduler(queue, ledger)
    node = make_node("node_1", cpu=2)
    scheduler.register_node(node)

    # 需求超过可用
    task = make_user_task("task_1", cpu=100)
    queue.enqueue(task)

    result = scheduler.schedule_next()
    assert result is None


def test_scheduler_node_timeout():
    """测试节点超时离线。"""
    queue = TaskQueue()
    ledger = Ledger()
    scheduler = Scheduler(queue, ledger)
    node = make_node("node_1")
    scheduler.register_node(node)

    # 模拟超时
    node.last_heartbeat = 0  # 很久以前
    offline = scheduler.check_node_timeout()
    assert "node_1" in offline
    assert node.status == NodeStatus.OFFLINE


def test_scheduler_preempt_fillers():
    """测试抢占填充任务。"""
    queue = TaskQueue()
    ledger = Ledger()
    scheduler = Scheduler(queue, ledger)
    node = make_node("node_1", cpu=4)
    scheduler.register_node(node)

    # 放一个填充任务并标记为运行中
    filler = Task(task_id="filler_1", task_type=TaskType.FILLER,
                  requirement=Resources(cpu_cores_total=2, memory_mb_total=256))
    queue.enqueue(filler)
    queue.update_status("filler_1", TaskStatus.RUNNING, "node_1")
    node.running_tasks.append("filler_1")

    # 抢占
    count = scheduler.preempt_fillers_for_user_task()
    assert count == 1
    task = queue.get("filler_1")
    assert task.status == TaskStatus.PENDING  # 回到队列


def test_pool_status():
    """测试池状态。"""
    queue = TaskQueue()
    ledger = Ledger()
    scheduler = Scheduler(queue, ledger)
    node = make_node("node_1", cpu=8, gpu=1, mem=16384)
    scheduler.register_node(node)

    status = scheduler.get_pool_status()
    assert status["online_nodes"] == 1
    assert status["available_cpu_cores"] > 0
    assert status["available_gpu_count"] >= 0
