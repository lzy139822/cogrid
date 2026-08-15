"""Cogrid 节点 Agent。

跑在每个贡献者机器上，负责资源上报、本地负载监测、
强度档位控制、Docker 任务执行和探针响应。

子模块：
    - monitor:   本地负载监测与强度档位控制
    - reporter:  资源上报与心跳（HTTP 通信）
    - executor:  Docker 任务执行（含子进程降级）
    - main:      入口，asyncio 主循环
"""
