.PHONY: dev test lint install proto clean help

# 默认目标
help:
	@echo "Cogrid 开发命令："
	@echo "  make install  - 安装依赖"
	@echo "  make dev      - 拉起本地环境（协调器 + 3 Agent + 仪表盘）"
	@echo "  make test     - 运行所有测试"
	@echo "  make lint     - 代码检查"
	@echo "  make proto    - 生成 gRPC 代码"
	@echo "  make clean    - 清理构建产物"

# 安装依赖
install:
	@echo ">>> 安装 Python 依赖..."
	pip install -e coordinator/ -e agent/ -e cli/ --break-system-packages 2>/dev/null || true
	@echo ">>> 安装前端依赖..."
	cd dashboard && npm install 2>/dev/null || true
	@echo ">>> 完成"

# 拉起本地开发环境
dev:
	@echo ">>> 拉起 docker-compose 环境..."
	docker-compose up --build

# 运行测试
test:
	@echo ">>> 运行测试..."
	pytest tests/ -v

# 代码检查
lint:
	@echo ">>> Python lint..."
	ruff check coordinator/ agent/ cli/ tests/ 2>/dev/null || true
	@echo ">>> 前端 lint..."
	cd dashboard && npx eslint src/ 2>/dev/null || true

# 生成 gRPC 代码
proto:
	@echo ">>> 生成 gRPC 代码..."
	python -m grpc_tools.protoc -I proto/ --python_out=coordinator/grpc_gen/ --grpc_python_out=coordinator/grpc_gen/ proto/cogrid.proto 2>/dev/null || echo ">>> proto 尚未定义，跳过"

# 清理
clean:
	rm -rf __pycache__ *.pyc .pytest_cache .coverage htmlcov
	rm -rf dashboard/node_modules dashboard/dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo ">>> 清理完成"
