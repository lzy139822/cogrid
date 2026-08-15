# 贡献指南

感谢你对 Cogrid 的兴趣！这个项目为协作和中断而设计——你可以随时加入、随时暂停。以下是参与方式。

## 快速上手

### 环境要求

- Python 3.11+
- Docker（支持 nvidia-container-runtime 可选，用于 GPU 任务）
- Node.js 18+（仅开发仪表盘时需要）
- Make

### 本地开发

```bash
# 克隆仓库
git clone https://github.com/cogrid/cogrid.git
cd cogrid

# 安装依赖
make install

# 拉起本地环境（1 协调器 + 3 Agent + 仪表盘）
make dev

# 运行测试
make test

# 代码检查
make lint
```

## 开发流程

1. **认领任务**：在 GitHub Issues 中找一个标记 `good-first-issue` 的任务，评论认领
2. **创建分支**：`git checkout -b feat/your-feature`（使用 Conventional Commits 前缀）
3. **开发**：保持每次提交可运行，写测试
4. **提交**：使用 Conventional Commits 格式（见下文）
5. **PR**：确保 CI 通过，填写 PR 模板

## 提交规范

使用 [Conventional Commits](https://www.conventionalcommits.org/)：

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

类型：
- `feat`: 新功能
- `fix`: 修复 bug
- `docs`: 文档变更
- `refactor`: 重构（不改变行为）
- `test`: 测试相关
- `chore`: 构建/工具/依赖等杂项
- `adr`: 架构决策记录

示例：
```
feat(scheduler): 实现按比例分红调度器
fix(agent): 修复 GPU 检测在无 nvidia-driver 时的崩溃
docs(readme): 补充快速开始指南
```

## 代码规范

- Python：遵循 PEP 8，使用 `ruff` 检查
- TypeScript：遵循 ESLint 配置
- 每个模块目录有 `README.md` 说明职责、接口、如何测试
- 代码中的 TODO 带上下文：

```python
# TODO(scheduler): 实现抢占式回收，当前只支持弹性池降级。
# 下一步：在 scheduler.preempt() 中加份额主人优先级判断。
# 参考：docs/specs/2026-08-15-cogrid-design.md §2.1 抢占回收
```

## 架构决策记录（ADR）

当做出有意义的架构决策时，在 `docs/adr/` 下创建新的 ADR 文件：

```
# ADR-XXX: 决策标题
## 状态：已接受 / 已废弃 / 已替代
## 背景：为什么需要这个决策
## 决策：选了什么
## 后果：带来什么利弊
```

## 项目状态同步

每次提交后，如果涉及模块完成度变化或下一步方向调整，请更新 [PROJECT_STATUS.md](PROJECT_STATUS.md)。

## 为协作与中断而设计

这个项目的设计原则是：**任何阶段都可以暂停、重启、终止，半成品可被后来者理解和接续。**

- 主分支任何 commit 都可 `make dev` 拉起完整环境
- 半成品功能用 feature flag 隔离，不破坏整体可用性
- 接口先于实现：即使实现是 stub，接口和文档注释也要完整
- 每个模块有清晰的接口定义，模块间通过接口通信

如果你需要暂停一个未完成的功能，请：
1. 确保代码能编译/运行（用 stub 或 feature flag）
2. 在 PROJECT_STATUS.md 中记录当前进度和下一步
3. 在代码中留下带上下文的 TODO

## License

贡献的代码将在 [MIT License](LICENSE) 下发布。
