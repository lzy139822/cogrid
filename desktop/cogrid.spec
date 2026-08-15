# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 — Cogrid 桌面版一体化 exe。

使用方式：
    pyinstaller desktop/cogrid.spec

生成的 exe 在 dist/ 目录下。

打包内容：
- 协调器（FastAPI + uvicorn）
- Agent（psutil + httpx + typer）
- CLI（httpx + typer + rich）
- 仪表盘静态文件（预构建到 dashboard/dist/）
- 系统托盘（pystray + Pillow）
"""

import os
from pathlib import Path

block_cipher = None

# 项目根目录
PROJECT_ROOT = Path(SPECPATH).parent if SPECPATH else Path.cwd()
if not (PROJECT_ROOT / "coordinator").exists():
    PROJECT_ROOT = Path.cwd()

# 仪表盘静态文件目录（需先 npm run build）
dashboard_dist = PROJECT_ROOT / "dashboard" / "dist"

a = Analysis(
    ["desktop/launcher.py"],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        # 打包仪表盘静态文件（如果已构建）
        (str(dashboard_dist), "dashboard/dist") if dashboard_dist.exists() else (),
    ],
    hiddenimports=[
        # FastAPI / uvicorn
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "fastapi",
        "fastapi.middleware",
        "pydantic",
        "aiosqlite",
        # Agent
        "psutil",
        "httpx",
        "typer",
        "rich",
        # 系统托盘
        "pystray",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        # 项目模块
        "coordinator",
        "coordinator.api",
        "coordinator.models",
        "coordinator.models.node",
        "coordinator.models.task",
        "coordinator.models.user",
        "coordinator.models.contribution",
        "coordinator.scheduler",
        "coordinator.queue",
        "coordinator.ledger",
        "coordinator.prober",
        "coordinator.filler",
        "coordinator.storage",
        "coordinator.auth",
        "coordinator.checkpoint",
        "agent",
        "agent.main",
        "agent.reporter",
        "agent.monitor",
        "agent.executor",
        "cli",
        "cli.main",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除不需要的大模块
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "tkinter",
        "test",
        "unittest",
    ],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="cogrid",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 无控制台窗口（--windowed）
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "desktop" / "cogrid.ico") if (PROJECT_ROOT / "desktop" / "cogrid.ico").exists() else None,
)
