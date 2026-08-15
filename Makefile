.PHONY: dev test lint install proto clean help docker-build docker-up docker-down docker-test e2e desktop desktop-clean

# 默认目标
help:
	@echo "Cogrid 开发命令："
	@echo "  make install      - 安装 Python + 前端依赖"
	@echo "  make dev          - 拉起 docker-compose 环境（协调器 + 3 Agent + 仪表盘）"
	@echo "  make test         - 运行所有单元测试"
	@echo "  make e2e          - 运行端到端实测（需先启动协调器）"
	@echo "  make docker-build - 构建 Docker 镜像"
	@echo "  make docker-up    - 拉起 Docker 容器"
	@echo "  make docker-down  - 停止 Docker 容器"
	@echo "  make docker-test  - Docker 环境完整测试"
	@echo "  make desktop      - 构建 Windows 桌面 exe"
	@echo "  make lint         - 代码检查"
	@echo "  make proto        - 生成 gRPC 代码"
	@echo "  make clean        - 清理构建产物"

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

# 端到端实测（需先启动协调器）
e2e:
	@echo ">>> 端到端实测（请确保协调器已运行在 localhost:8000）..."
	python tests/run_e2e.py

# Docker 镜像构建
docker-build:
	@echo ">>> 构建 Docker 镜像..."
	docker build -f Dockerfile.coordinator -t cogrid-coordinator:latest .
	docker build -f Dockerfile.agent -t cogrid-agent:latest .
	cd dashboard && docker build -t cogrid-dashboard:latest .

# 拉起 Docker 容器
docker-up:
	@echo ">>> 拉起 Docker 容器..."
	docker-compose up --build

# 停止 Docker 容器
docker-down:
	@echo ">>> 停止 Docker 容器..."
	docker-compose down

# Docker 环境完整测试
docker-test:
	@echo ">>> Docker 环境完整测试..."
	@echo "1. 构建镜像..."
	docker-compose build
	@echo "2. 拉起容器..."
	docker-compose up -d
	@echo "3. 等待服务就绪..."
	sleep 10
	@echo "4. 检查服务状态..."
	docker-compose ps
	@echo "5. 健康检查..."
	curl -s http://localhost:8000/api/v1/health || echo "协调器未就绪"
	@echo ""
	@echo "6. 查看日志..."
	docker-compose logs --tail=20 coordinator
	@echo ">>> 测试完成。访问 http://localhost:3000 查看仪表盘"

# 构建 Windows 桌面 exe
desktop:
	@echo ">>> 构建桌面版 exe..."
	@echo "1. 安装依赖..."
	pip install pyinstaller pystray pillow --break-system-packages 2>/dev/null || true
	pip install -e coordinator/ -e agent/ -e cli/ --break-system-packages 2>/dev/null || true
	@echo "2. 构建仪表盘..."
	cd dashboard && npm install 2>/dev/null && npm run build 2>/dev/null || echo "跳过仪表盘构建"
	cd ..
	@echo "3. PyInstaller 打包..."
	pyinstaller desktop/cogrid.spec --noconfirm --clean
	@echo ">>> 完成！输出: dist/cogrid.exe"

# 清理桌面版构建产物
desktop-clean:
	rm -rf build/ dist/ *.spec.bak
	@echo ">>> 桌面版构建产物已清理"

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
