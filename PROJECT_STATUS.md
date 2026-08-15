# 项目状态看板

> 这个文件是项目的活的状态看板。每次提交后更新。新贡献者打开仓库，第一眼看这个文件就知道项目到哪了、能帮什么。

**最后更新**：2026-08-15

## 当前阶段

点火开发中 — 协调器 + Agent + CLI + 仪表盘 + 持久化存储已实现

**仓库地址**：https://github.com/lzy139822/cogrid

## 已完成

| 模块 | 完成度 | 说明 |
|---|---|---|
| 设计文档 | 100% | [docs/specs/2026-08-15-cogrid-design.md](docs/specs/2026-08-15-cogrid-design.md) |
| 架构决策 ADR-001 | 100% | Python/FastAPI 架构选型 |
| 架构决策 ADR-002 | 100% | 算力固存机制 |
| 协调器核心 | 95% | 节点注册/心跳、贡献账本、调度器、任务队列、PoA探针、算力固存、REST API — 已通过端到端实测 |
| SQLite 持久化 | 90% | Storage 层 + Ledger/Queue 持久化注入 + 重启恢复验证通过 |
| Python Agent | 90% | 资源上报、负载监测、强度档位、Docker/子进程执行、探针响应 — 已通过端到端实测 |
| CLI 客户端 | 85% | register/submit/status/tasks/contribution/leaderboard/pool/nodes/intensity |
| Web 仪表盘 | 85% | React+Vite+TS+Tailwind，5个页面（概览/节点/排行榜/任务/提交），深色主题，3秒轮询，移动端适配 |
| 单元测试 | 85% | 34 个测试全部通过（账本、调度器、队列、探针、固存、持久化） |
| Docker | 75% | Dockerfile.coordinator + Dockerfile.agent + dashboard/Dockerfile + docker-compose.yml |

## 点火验证结果

端到端实测已通过（协调器 + Agent + 持久化）：
1. **消费链路**：注册节点 → 提交任务 → 调度器分配 → 节点收到任务 ✓
2. **固存链路**：节点挂机 → PoA探针自动派发 → 填充任务自动生成 → 贡献分累积 ✓
3. **贡献系统**：贡献分按资源量×时长×探针成功率累积，份额比例正确计算 ✓
4. **持久化恢复**：协调器重启 → SQLite自动恢复贡献记录和未完成任务 ✓
5. **Agent 联调**：Agent注册→心跳→领取探针+填充任务→子进程执行→结果上报→贡献分累积 ✓
6. **多节点模拟**：3节点(保守/均衡/激进) → 各自领取任务 → 份额按贡献分配 ✓

## 下一个接手者应从哪里开始

1. 阅读 [设计文档](docs/specs/2026-08-15-cogrid-design.md) 了解全貌
2. 运行 `make test` 确认 34 个测试通过
3. 运行 `docker-compose up --build` 拉起完整环境（协调器+3Agent+仪表盘）
4. 下一步重点：
   - 仪表盘 Docker 构建实测（需 Docker 环境）
   - Agent 实际 Docker 执行测试（当前用子进程降级，需有 Docker 的环境验证）
   - gRPC 迁移（proto 已定义）
   - 用户认证与多租户
   - 抢占式调度实测

## 点火 MVP 待办

按优先级排序：

- [ ] 协调器：节点注册/心跳、贡献账本（含 PoA 探针成功率）、按比例调度器、任务队列、PoA 探针派发、REST API
- [ ] Python Agent：资源上报、本地负载监测、强度档位、Docker 任务执行（CPU+GPU）、探针响应、结果回传
- [ ] CLI：注册节点、提交任务、查状态、查贡献、设强度档位
- [ ] 仪表盘：池概览、节点列表、贡献榜、任务状态、固存产物浏览
- [ ] 算力固存：基础填充任务调度器（镜像预构建+预计算缓存），含抢占让出
- [ ] 本地演示：docker-compose 端到端跑通（含空闲固存演示）
- [ ] 文档完善：README 补充使用说明、ADR-003+

## 已知问题与待决事项

- 贡献分质量系数的具体公式（先用简单值，跑起来再调）
- PoA 探针频率与探针任务内容（先用固定 5 分钟 + 小 benchmark）
- 填充任务的产物淘汰策略
- 社区模型训练的目标模型与训练数据（需社区讨论）
- 弹性池抢占的具体优先级算法

## 如何认领任务

1. 在 GitHub Issues 中找到标记为 `good-first-issue` 的任务
2. 评论 "I'd like to work on this" 认领
3. 创建 feature branch 开始开发
4. 开发完成后提 PR，CI 通过即可合并
