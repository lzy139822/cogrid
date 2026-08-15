# Cogrid 桌面版

> 一键打包为 Windows 桌面 exe，双击即用，无需安装 Python/Node.js。

## 快速构建

### 前置条件

- Windows 10/11
- Python 3.11+（[下载](https://www.python.org/downloads/)）
- Node.js 18+（[下载](https://nodejs.org/)，用于构建仪表盘）

### 一键构建

```powershell
cd D:\cogrid
desktop\build.bat
```

构建完成后，`dist\cogrid.exe` 就是完整的一体化桌面应用。

### 手动构建

```powershell
# 1. 安装依赖
pip install pyinstaller pystray pillow
pip install -e coordinator/ -e agent/ -e cli/

# 2. 构建仪表盘
cd dashboard && npm install && npm run build && cd ..

# 3. 打包
pyinstaller desktop/cogrid.spec --noconfirm --clean

# 4. 完成 — dist\cogrid.exe
```

## 使用方式

双击 `cogrid.exe`，程序会自动：

1. 启动协调器（后台 FastAPI 服务）
2. 启动本地 Agent（贡献算力，保守模式）
3. 打开浏览器访问仪表盘
4. 在系统托盘显示图标

### 系统托盘菜单

- **打开仪表盘** — 重新打开浏览器
- **退出** — 停止所有服务并退出

### 访问地址

| 服务 | 地址 |
|---|---|
| 仪表盘 | http://127.0.0.1:8000 |
| API 文档 | http://127.0.0.1:8000/docs |

## 架构说明

```
cogrid.exe（单文件，约 50-80MB）
├── 协调器（FastAPI + uvicorn）
│   ├── REST API（/api/v1/*）
│   ├── SQLite 持久化（~/.cogrid/cogrid.db）
│   └── 仪表盘静态文件托管（/）
├── 本地 Agent
│   ├── 资源检测（psutil）
│   ├── 任务执行（子进程/Docker）
│   └── 心跳上报
└── 系统托盘（pystray）
    ├── 打开仪表盘
    └── 退出
```

## 打包选项

### 单文件模式（默认）

```powershell
pyinstaller desktop/cogrid.spec --onefile
```
- 优点：单文件分发方便
- 缺点：启动稍慢（需解压临时目录）

### 目录模式

修改 `cogrid.spec` 中的 `EXE` 为 `COLLECT`：
```python
exe = EXE(...)  # 改为 COLLECT
```
- 优点：启动快
- 缺点：多文件目录

## 分发

打包后的 `cogrid.exe` 可直接复制给其他 Windows 用户使用，无需任何额外安装。

建议打包为 zip 分发：
```powershell
Compress-Archive -Path dist\cogrid.exe -DestinationPath cogrid-windows-x64.zip
```

## 注意事项

- exe 首次启动较慢（解压临时目录），后续启动正常
- 数据存储在 `%USERPROFILE%\.cogrid\` 目录
- 如需 GPU 支持，目标机器需安装 NVIDIA 驱动
- 杀毒软件可能误报 PyInstaller 生成的 exe，需添加白名单
