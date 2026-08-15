# ADR-001: Python/FastAPI 协调器 + 开放协议 Agent

## 状态

已接受

## 背景

Cogrid 需要选择协调器（Coordinator）和节点 Agent 的技术栈。关键约束：

1. 上手简单、改代码方便（开发者最看重）
2. Go 和 Python 社区都要能参与（双社区友好）
3. 可发布到 GitHub 吸引贡献者
4. 支持混合 CPU/GPU 算力共享
5. 任务以 Docker 容器执行（语言无关）

## 决策

- **协调器/调度器/账本/仪表盘后端用 Python(FastAPI)**：无编译、可读性强、pip 即装即跑，最契合"改代码方便、上手简单"的要求。
- **节点 Agent 走明确的 gRPC/HTTP 协议契约**：任何语言都能实现 Agent。先交付 Python 参考版 Agent（易改），后续社区可贡献 Go 单二进制版 Agent（易部署）。
- **任务执行统一走 Docker 容器**：CPU/GPU 用 runtime 区分，调度器不关心任务语言。

## 后果

**优势：**
- 上手简单：Python 无编译，改代码即生效
- 双社区：Python 参考版降低门槛，Go 版后续贡献
- 协议开放：proto/ 定义跨语言契约，不绑定单一语言
- Docker 统一执行层：隔离、安全、语言无关

**风险：**
- Python 性能不如 Go——但协调器是 IO 密集型（调度、记账、API），Python asyncio 足够
- 维护两套语言 Agent——但协议契约统一，参考版只需一个
- 需要安装 Python 运行时——相比 Go 单二进制部署成本高，但开发体验更好
