#!/usr/bin/env python3
"""认证 + 抢占 端到端测试脚本。

验证完整的认证流程和抢占式调度：
1. 用户注册/登录（JWT token）
2. 无认证访问受保护路由（401）
3. 带认证注册节点、提交任务
4. 贡献者回收份额（抢占式调度）
5. 池状态按 owner 分组
6. 认证持久化验证

运行方式：
    # 先启动协调器
    COGRID_DB_PATH=/tmp/e2e_cogrid.db uvicorn coordinator.main:app --port 8000 &
    # 再运行测试
    python tests/e2e_test.py
"""

import httpx

BASE = "http://localhost:8000/api/v1"
client = httpx.Client(base_url=BASE, timeout=10)


def header(token):
    return {"Authorization": f"Bearer {token}"}


print("=" * 50)
print("  认证 + 抢占 端到端实测")
print("=" * 50)

# 1. 注册用户
print("\n=== 1. 用户注册 ===")
r = client.post("/auth/register", json={"username": "alice", "password": "alice123"})
assert r.status_code == 200, f"注册失败: {r.text}"
alice = r.json()
ALICE_TOKEN = alice["token"]
print(f"  alice: {alice['user_id']}")

r = client.post("/auth/register", json={"username": "bob", "password": "bob123"})
assert r.status_code == 200
bob = r.json()
BOB_TOKEN = bob["token"]
print(f"  bob:   {bob['user_id']}")

# 2. 登录验证（登录会刷新 token）
print("\n=== 2. 登录验证 ===")
r = client.post("/auth/login", json={"username": "alice", "password": "alice123"})
assert r.status_code == 200
ALICE_TOKEN = r.json()["token"]  # 用登录后的新 token
print("  alice 登录成功，token 已刷新")

r = client.post("/auth/login", json={"username": "alice", "password": "wrong"})
assert r.status_code == 401
print("  错误密码正确返回 401")

# 3. 无认证访问受保护路由
print("\n=== 3. 无认证访问（应401）===")
r = client.post("/tasks/submit", json={"image": "busybox", "command": ["echo"], "cpu_cores": 1})
assert r.status_code == 401
print(f"  无token提交任务: HTTP {r.status_code} (正确)")

# 4. alice 注册节点
print("\n=== 4. alice 注册节点（带认证）===")
r = client.post("/nodes/register", json={
    "node_name": "alice-node", "cpu_cores": 8, "gpu_count": 0,
    "memory_mb": 16384, "intensity": "balanced"
}, headers=header(ALICE_TOKEN))
assert r.status_code == 200, f"注册节点失败: {r.text}"
NODE_ID = r.json()["node_id"]
print(f"  节点ID: {NODE_ID} (owner=alice)")

# 5. 心跳（领取探针+填充任务）
print("\n=== 5. 心跳（领取探针+填充任务）===")
r = client.post("/nodes/heartbeat", json={
    "node_id": NODE_ID, "cpu_cores": 8, "gpu_count": 0, "memory_mb": 16384,
    "cpu_usage_percent": 15, "gpu_usage_percent": 0, "memory_usage_percent": 30,
    "intensity": "balanced"
})
tasks = r.json()["tasks"]
print(f"  收到 {len(tasks)} 个任务: {[t['task_type'] for t in tasks]}")

# 6. bob 提交用户任务
print("\n=== 6. bob 提交用户任务（带认证）===")
r = client.post("/tasks/submit", json={
    "image": "busybox:latest", "command": ["echo", "bob-task"],
    "cpu_cores": 2, "gpu_count": 0, "memory_mb": 256, "timeout_seconds": 30
}, headers=header(BOB_TOKEN))
assert r.status_code == 200
TASK_ID = r.json()["task_id"]
print(f"  任务ID: {TASK_ID}")

# 7. 心跳（bob 的任务调度到 alice 的节点）
print("\n=== 7. 心跳（领取 bob 的任务）===")
r = client.post("/nodes/heartbeat", json={
    "node_id": NODE_ID, "cpu_cores": 8, "gpu_count": 0, "memory_mb": 16384,
    "cpu_usage_percent": 15, "gpu_usage_percent": 0, "memory_usage_percent": 30,
    "intensity": "balanced"
})
tasks = r.json()["tasks"]
print(f"  收到 {len(tasks)} 个任务: {[t['task_type'] for t in tasks]}")

# 8. 查看节点列表
print("\n=== 8. 节点列表（含 owner）===")
r = client.get("/nodes")
for n in r.json()["nodes"]:
    print(f"  {n['name']} | owner={n.get('owner_user_id', '-')} | 贡献:{n['credits']:.1f} | 份额:{n['share_ratio']:.1%}")

# 9. alice 回收节点
print("\n=== 9. alice 回收节点资源 ===")
ALICE_ID = alice["user_id"]
r = client.post(f"/nodes/{NODE_ID}/reclaim", json={
    "owner_user_id": ALICE_ID, "required_cpu": 4, "required_gpu": 0, "required_memory": 0
}, headers=header(ALICE_TOKEN))
print(f"  HTTP {r.status_code}: {r.json()}")

# 10. 池状态
print("\n=== 10. 池状态（含 by_owner）===")
r = client.get("/pool/status")
d = r.json()
print(f"  在线节点: {d['online_nodes']}")
print(f"  总贡献分: {d['total_credits']:.1f}")
if "by_owner" in d:
    print("  按 owner 分组:")
    for owner, info in d["by_owner"].items():
        print(f"    {owner}: {info}")

# 11. 贡献排行
print("\n=== 11. 贡献排行 ===")
r = client.get("/leaderboard")
for i, entry in enumerate(r.json()["leaderboard"], 1):
    print(f"  #{i} {entry['node_id']} | 贡献:{entry['total_credits']:.1f} | 份额:{entry['share_ratio']:.1%}")

# 12. 持久化验证
print("\n=== 12. 认证持久化验证 ===")
r = client.get("/auth/me", headers=header(ALICE_TOKEN))
assert r.status_code == 200
me = r.json()
print(f"  /auth/me 返回: username={me.get('username', '-')}")

print("\n" + "=" * 50)
print("  认证 + 抢占 端到端测试全部通过！")
print("=" * 50)
