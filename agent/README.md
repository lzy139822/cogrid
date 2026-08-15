# 节点 Agent

跑在每个贡献者机器上，负责资源上报、本地负载监测、强度档位控制、Docker 任务执行和探针响应。

## 模块

| 文件 | 职责 | 状态 |
|---|---|---|
| `reporter.py` | 资源上报与心跳 | 待实现 |
| `monitor.py` | 本地负载监测与强度档位控制 | 待实现 |
| `executor.py` | Docker 任务执行（含探针响应） | 待实现 |
| `main.py` | 入口 | 待实现 |

## 强度档位

| 档位 | 行为 |
|---|---|
| 保守 | 仅在本地完全空闲时贡献 |
| 均衡 | 留 30% 资源余量 |
| 激进 | 留 10% 资源余量 |

档位可通过 CLI 实时调整，无需重启 Agent。

## 开发

```bash
# 启动 Agent（连接本地协调器）
python -m agent.main --coordinator localhost:50051 --node-name my-node
```
