# Docker 环境安装与实测指南（Windows D 盘）

> 本指南帮助你在 Windows 系统 D 盘安装 Docker Desktop，并完成 Cogrid 项目的完整 Docker 实测。

## 一、安装 Docker Desktop 到 D 盘

### 1.1 下载 Docker Desktop

从官方下载安装程序：https://www.docker.com/products/docker-desktop/

### 1.2 安装到 D 盘

Docker Desktop 默认安装到 C 盘，但数据目录可以迁移到 D 盘：

**方法一：安装时指定（推荐）**

1. 正常安装 Docker Desktop 到默认位置（C 盘的安装文件不大）
2. 安装完成后，将 WSL2 数据目录迁移到 D 盘

**方法二：迁移 WSL2 数据到 D 盘**

```powershell
# 1. 关闭 Docker Desktop
# 2. 关闭 WSL
wsl --shutdown

# 3. 导出 docker-desktop-data 到 D 盘
wsl --export docker-desktop-data D:\DockerData\docker-desktop-data.tar

# 4. 注销原来的
wsl --unregister docker-desktop-data

# 5. 重新导入到 D 盘
wsl --import docker-desktop-data D:\DockerData\docker-desktop-data D:\DockerData\docker-desktop-data.tar

# 6. 清理临时文件
del D:\DockerData\docker-desktop-data.tar

# 7. 重启 Docker Desktop
```

### 1.3 配置 Docker 镜像存储路径

打开 Docker Desktop → Settings → Resources → Disk image location：
```
D:\DockerData\images
```

### 1.4 验证安装

```powershell
docker --version
docker run hello-world
```

## 二、获取 Cogrid 项目

```powershell
cd D:\
git clone https://github.com/lzy139822/cogrid.git
cd cogrid
```

## 三、Docker 实测

### 3.1 一键启动（推荐）

```powershell
cd D:\cogrid
docker-compose up --build
```

等待构建完成（首次约 5-10 分钟），看到以下输出表示成功：
```
✓ coordinator  | INFO: Cogrid 协调器就绪 — 等待节点连接
✓ agent-1      | 节点注册成功
✓ agent-2      | 节点注册成功
✓ agent-3      | 节点注册成功
✓ dashboard    | nginx ready
```

### 3.2 验证服务

打开浏览器访问：

| 服务 | 地址 | 说明 |
|---|---|---|
| 仪表盘 | http://localhost:3000 | 可视化界面 |
| API 文档 | http://localhost:8000/docs | Swagger UI |
| 健康检查 | http://localhost:8000/api/v1/health | JSON 响应 |

### 3.3 使用 CLI 测试

```powershell
# 安装 CLI
cd D:\cogrid
pip install -e cli/

# 注册用户
cogrid register-user --username alice --password alice123

# 查看当前用户
cogrid me

# 注册节点（需先登录）
cogrid register --name my-node

# 提交任务
cogrid submit --image busybox:latest --cpu 1 -- echo "Hello Cogrid"

# 查看任务状态
cogrid tasks

# 查看贡献排行
cogrid leaderboard

# 查看算力池状态
cogrid pool

# 查看节点列表
cogrid nodes

# 回收节点资源
cogrid reclaim --node <node_id> --cpu 2
```

### 3.4 认证 + 抢占端到端测试

```powershell
# 1. 先确保协调器在运行（docker-compose up）

# 2. 运行端到端测试脚本
cd D:\cogrid
pip install httpx
python tests/run_e2e.py
```

预期输出：
```
=== 1. 用户注册 ===
  alice: user_xxx
  bob:   user_xxx
=== 2. 登录验证 ===
  alice 登录成功，token 已刷新
...
=== 9. alice 回收节点资源 ===
  HTTP 200: preempted_count: 3
...
  认证 + 抢占 端到端测试全部通过！
```

### 3.5 单独构建镜像

```powershell
# 构建协调器镜像
docker build -f Dockerfile.coordinator -t cogrid-coordinator:latest .

# 构建 Agent 镜像
docker build -f Dockerfile.agent -t cogrid-agent:latest .

# 构建仪表盘镜像
cd dashboard
docker build -t cogrid-dashboard:latest .
cd ..
```

### 3.6 单独运行容器

```powershell
# 运行协调器
docker run -d --name coordinator -p 8000:8000 \
  -v cogrid-data:/data \
  cogrid-coordinator:latest

# 运行 Agent（需先注册用户或关闭认证）
docker run -d --name agent-1 \
  -e COGRID_COORDINATOR_URL=http://host.docker.internal:8000/api/v1 \
  -e COGRID_NODE_NAME=agent-1 \
  -e COGRID_INTENSITY=balanced \
  -e COGRID_USERNAME=agent1 \
  -e COGRID_PASSWORD=agent1pass \
  -v /var/run/docker.sock:/var/run/docker.sock \
  cogrid-agent:latest

# 运行仪表盘
docker run -d --name dashboard -p 3000:80 \
  cogrid-dashboard:latest
```

## 四、关闭认证（测试用）

如果不想配置认证，可以在 docker-compose.yml 中取消注释：

```yaml
coordinator:
  environment:
    - COGRID_AUTH_DISABLED=1
```

这样所有 API 请求无需 token，适合快速测试。

## 五、常见问题

### Q: Agent 连不上协调器？

A: Windows 下使用 `host.docker.internal` 替代 `localhost`：
```yaml
environment:
  - COGRID_COORDINATOR_URL=http://host.docker.internal:8000/api/v1
```

### Q: Docker 构建很慢？

A: 配置国内镜像加速：
Docker Desktop → Settings → Docker Engine：
```json
{
  "registry-mirrors": [
    "https://mirror.ccs.tencentyun.com",
    "https://docker.mirrors.ustc.edu.cn"
  ]
}
```

### Q: 磁盘空间不足？

A: 定期清理：
```powershell
docker system prune -a
```

### Q: 端口冲突？

A: 修改 docker-compose.yml 中的端口映射：
```yaml
ports:
  - "8001:8000"  # 协调器改用 8001
  - "3001:80"    # 仪表盘改用 3001
```

## 六、停止和清理

```powershell
# 停止容器
docker-compose down

# 停止并删除数据卷（清除所有持久化数据）
docker-compose down -v

# 清理镜像
docker rmi cogrid-coordinator cogrid-agent cogrid-dashboard
```
