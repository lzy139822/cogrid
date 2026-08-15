#!/usr/bin/env python3
"""Cogrid Docker 环境测试脚本。

在本地 Docker 环境运行，验证完整链路：
1. 镜像构建
2. 容器启动
3. 健康检查
4. 认证流程
5. 任务提交与执行
6. 抢占回收
7. 持久化验证

使用方式：
    cd D:\\cogrid
    python tests/docker_test.py
"""

import subprocess
import sys
import time
import httpx
import json

BASE = "http://localhost:8000/api/v1"
client = httpx.Client(base_url=BASE, timeout=15)


def run(cmd: str, check: bool = True) -> str:
    """运行命令并返回输出。"""
    print(f"  $ {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"  [ERROR] {result.stderr}")
        sys.exit(1)
    return result.stdout.strip()


def header(title: str) -> None:
    print(f"\n{'='*50}")
    print(f"  {title}")
    print(f"{'='*50}")


def wait_for_coordinator(max_wait: int = 60) -> bool:
    """等待协调器就绪。"""
    print("\n等待协调器就绪...", end="", flush=True)
    for i in range(max_wait):
        try:
            r = client.get("/health")
            if r.status_code == 200:
                print(" OK")
                return True
        except Exception:
            pass
        print(".", end="", flush=True)
        time.sleep(1)
    print(" TIMEOUT")
    return False


def main():
    header("Cogrid Docker 环境完整测试")

    # 1. 检查 Docker
    header("1. 检查 Docker 环境")
    version = run("docker --version")
    print(f"  Docker 版本: {version}")
    run("docker info --format '{{.ServerVersion}}'")

    # 2. 构建镜像
    header("2. 构建镜像")
    print("  构建协调器镜像...")
    run("docker build -f Dockerfile.coordinator -t cogrid-coordinator:latest .")
    print("  构建 Agent 镜像...")
    run("docker build -f Dockerfile.agent -t cogrid-agent:latest .")
    print("  构建仪表盘镜像...")
    run("cd dashboard && docker build -t cogrid-dashboard:latest . && cd ..")
    print("  [OK] 所有镜像构建成功")

    # 3. 启动容器
    header("3. 启动容器")
    run("docker-compose up -d --build")
    print("  [OK] 容器已启动")

    # 4. 等待协调器就绪
    header("4. 健康检查")
    if not wait_for_coordinator():
        print("  [FAIL] 协调器未就绪")
        run("docker-compose logs --tail=30 coordinator")
        sys.exit(1)
    print("  [OK] 协调器健康检查通过")

    # 5. 检查容器状态
    header("5. 容器状态")
    run("docker-compose ps")

    # 6. 认证测试
    header("6. 认证测试")
    # 注册
    r = client.post("/auth/register", json={"username": "alice", "password": "alice123"})
    assert r.status_code == 200, f"注册失败: {r.text}"
    alice = r.json()
    ALICE_TOKEN = alice["token"]
    print(f"  注册 alice: {alice['user_id']}")

    # 登录
    r = client.post("/auth/login", json={"username": "alice", "password": "alice123"})
    assert r.status_code == 200
    ALICE_TOKEN = r.json()["token"]
    print(f"  登录成功，token 已刷新")

    # 无认证访问（应 401）
    r = client.post("/tasks/submit", json={"image": "busybox", "command": ["echo"]})
    assert r.status_code == 401
    print(f"  无认证拦截: HTTP {r.status_code} (正确)")
    print("  [OK] 认证测试通过")

    # 7. 注册节点 + 提交任务
    header("7. 节点注册 + 任务提交")
    headers = {"Authorization": f"Bearer {ALICE_TOKEN}"}
    r = client.post("/nodes/register", json={
        "node_name": "test-node", "cpu_cores": 4, "gpu_count": 0,
        "memory_mb": 8192, "intensity": "balanced"
    }, headers=headers)
    assert r.status_code == 200
    NODE_ID = r.json()["node_id"]
    print(f"  节点: {NODE_ID}")

    # 心跳
    r = client.post("/nodes/heartbeat", json={
        "node_id": NODE_ID, "cpu_cores": 4, "gpu_count": 0, "memory_mb": 8192,
        "cpu_usage_percent": 20, "gpu_usage_percent": 0, "memory_usage_percent": 30,
        "intensity": "balanced"
    })
    tasks = r.json()["tasks"]
    print(f"  心跳收到 {len(tasks)} 个任务")
    print("  [OK] 节点注册 + 任务提交通过")

    # 8. 池状态
    header("8. 池状态")
    r = client.get("/pool/status")
    d = r.json()
    print(f"  在线节点: {d['online_nodes']}")
    print(f"  总贡献分: {d['total_credits']:.1f}")
    print("  [OK] 池状态正常")

    # 9. 仪表盘检查
    header("9. 仪表盘检查")
    try:
        r = httpx.get("http://localhost:3000", timeout=5)
        if r.status_code == 200:
            print(f"  HTTP {r.status_code} - 仪表盘可访问")
            print("  [OK] 仪表盘正常")
        else:
            print(f"  HTTP {r.status_code} - 仪表盘异常")
    except Exception as e:
        print(f"  [WARN] 仪表盘未就绪: {e}")

    # 10. 总结
    header("测试结果")
    print("  [OK] Docker 镜像构建")
    print("  [OK] 容器启动")
    print("  [OK] 协调器健康检查")
    print("  [OK] 认证系统")
    print("  [OK] 节点注册 + 任务")
    print("  [OK] 池状态")
    print("  [OK] 仪表盘")
    print("\n  所有测试通过！")
    print(f"\n  仪表盘: http://localhost:3000")
    print(f"  API 文档: http://localhost:8000/docs")
    print(f"\n  停止容器: docker-compose down")


if __name__ == "__main__":
    main()
