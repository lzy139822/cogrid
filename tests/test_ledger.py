"""贡献值账本测试。"""

import time
import pytest
from coordinator.ledger import Ledger
from coordinator.models.node import Node, Resources, IntensityLevel, NodeStatus


def make_node(node_id: str, cpu: int = 8, gpu: int = 0, mem: int = 16384) -> Node:
    """创建测试节点。"""
    return Node(
        node_id=node_id,
        name=f"test-{node_id}",
        resources=Resources(cpu_cores_total=cpu, gpu_count_total=gpu, memory_mb_total=mem),
        intensity=IntensityLevel.BALANCED,
        status=NodeStatus.ONLINE,
    )


def test_create_record():
    """测试创建贡献记录。"""
    ledger = Ledger()
    rec = ledger.get_or_create("node_1")
    assert rec.total_credits == 0.0
    assert rec.probe_success_rate == 1.0  # 默认满分


def test_record_probe():
    """测试探针记录。"""
    ledger = Ledger()
    ledger.record_probe("node_1", True)
    ledger.record_probe("node_1", True)
    ledger.record_probe("node_1", False)
    rec = ledger.get_or_create("node_1")
    assert rec.probe_total_count == 3
    assert rec.probe_success_count == 2
    assert rec.probe_success_rate == pytest.approx(2 / 3)


def test_settle_credits():
    """测试贡献分结算。"""
    ledger = Ledger()
    node = make_node("node_1", cpu=8)
    # 先创建记录（get_or_create 会初始化 _last_settle 为 now）
    ledger.get_or_create("node_1")
    # 然后手动设置上次结算时间为过去，确保有时间差
    ledger._last_settle["node_1"] = time.time() - 10  # 10 秒前
    credits = ledger.settle_online_credits(node)
    assert credits > 0
    rec = ledger.get_or_create("node_1")
    assert rec.total_credits > 0
    assert rec.online_seconds >= 9  # 至少 9 秒


def test_share_ratio():
    """测试份额比例计算。"""
    ledger = Ledger()
    # 两个节点贡献不同量
    node1 = make_node("node_1", cpu=8)
    node2 = make_node("node_2", cpu=4)
    ledger._last_settle["node_1"] = time.time() - 10
    ledger._last_settle["node_2"] = time.time() - 10
    ledger.settle_online_credits(node1)
    ledger.settle_online_credits(node2)

    ratio1 = ledger.get_share_ratio("node_1")
    ratio2 = ledger.get_share_ratio("node_2")
    # node1 有 8 核，node2 有 4 核，比例约 2:1
    assert ratio1 > ratio2
    assert ratio1 + ratio2 == pytest.approx(1.0, rel=0.01)


def test_probe_affects_credits():
    """探针成功率影响贡献分。"""
    ledger = Ledger()
    node = make_node("node_1", cpu=8)
    # 记录一些失败的探针
    for _ in range(5):
        ledger.record_probe("node_1", False)

    ledger._last_settle["node_1"] = time.time() - 10
    credits_with_failures = ledger.settle_online_credits(node)

    # 重置，全部成功
    ledger2 = Ledger()
    node2 = make_node("node_1", cpu=8)
    for _ in range(5):
        ledger2.record_probe("node_1", True)
    ledger2._last_settle["node_1"] = time.time() - 10
    credits_all_success = ledger2.settle_online_credits(node2)

    # 全部成功的贡献分应该更高
    assert credits_all_success > credits_with_failures


def test_leaderboard():
    """测试排行榜。"""
    ledger = Ledger()
    nodes = [make_node(f"node_{i}", cpu=i * 2 + 1) for i in range(5)]
    for node in nodes:
        ledger._last_settle[node.node_id] = time.time() - 10
        ledger.settle_online_credits(node)

    board = ledger.get_leaderboard(limit=3)
    assert len(board) == 3
    # CPU 最多的应该排第一
    assert board[0]["total_credits"] >= board[1]["total_credits"]
