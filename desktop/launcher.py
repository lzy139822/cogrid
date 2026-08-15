"""Cogrid 桌面版启动器 — 一体化 Windows 桌面应用入口。

功能：
- 后台启动协调器（FastAPI + uvicorn）
- 自动打开浏览器访问仪表盘
- 系统托盘图标控制（启动/停止/打开浏览器/退出）
- 可选启动本地 Agent 贡献算力

打包方式：
    pyinstaller desktop/launcher.py --onefile --windowed --icon cogrid.ico

运行方式：
    双击 cogrid.exe 即可启动完整环境，无需安装 Python 或 Node.js。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

logger = logging.getLogger(__name__)

# 确保 bundled 环境能找到项目模块
if getattr(sys, "frozen", False):
    # PyInstaller 打包后，模块在 _MEIPASS 临时目录
    BASE_DIR = Path(sys._MEIPASS)
    sys.path.insert(0, str(BASE_DIR))
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(BASE_DIR))


def start_coordinator(host: str = "127.0.0.1", port: int = 8000) -> threading.Thread:
    """在后台线程中启动协调器。

    Returns:
        守护线程对象
    """
    import uvicorn

    def _run():
        uvicorn.run(
            "coordinator.main:app",
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
        )

    thread = threading.Thread(target=_run, daemon=True, name="coordinator")
    thread.start()
    logger.info("协调器已启动: http://%s:%d", host, port)
    return thread


def wait_for_coordinator(port: int = 8000, max_wait: int = 15) -> bool:
    """等待协调器就绪。"""
    import httpx

    for _ in range(max_wait):
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=1)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def start_agent(port: int = 8000) -> threading.Thread | None:
    """在后台线程中启动本地 Agent。

    Returns:
        守护线程对象，启动失败返回 None
    """
    try:
        import asyncio
        import socket
        from agent.main import _run_agent
        from coordinator.models.node import IntensityLevel

        node_name = f"desktop-{socket.gethostname()}"

        def _run():
            asyncio.run(
                _run_agent(
                    coordinator_url=f"http://127.0.0.1:{port}/api/v1",
                    node_name=node_name,
                    intensity=IntensityLevel.CONSERVATIVE,
                    heartbeat_interval=30,
                )
            )

        thread = threading.Thread(target=_run, daemon=True, name="agent")
        thread.start()
        logger.info("本地 Agent 已启动: %s", node_name)
        return thread
    except Exception as e:
        logger.warning("启动 Agent 失败: %s", e)
        return None


def create_tray_icon(port: int = 8000):
    """创建系统托盘图标。

    使用 pystray + Pillow 创建跨平台系统托盘。
    PyInstaller 打包时需包含 pystray 和 Pillow。
    """
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        logger.warning("pystray/Pillow 未安装，跳过托盘图标")
        return None

    # 生成简单的图标（绿色圆形表示运行中）
    def _create_icon():
        img = Image.new("RGB", (64, 64), (30, 30, 46))
        draw = ImageDraw.Draw(img)
        draw.ellipse([12, 12, 52, 52], fill=(52, 211, 153))
        draw.text((22, 20), "CG", fill=(30, 30, 46))
        return img

    def _on_open(icon, item):
        webbrowser.open(f"http://127.0.0.1:{port}")

    def _on_quit(icon, item):
        icon.stop()
        import os
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("打开仪表盘", _on_open, default=True),
        pystray.MenuItem("退出", _on_quit),
    )

    try:
        icon = pystray.Icon(
            "cogrid",
            _create_icon(),
            "Cogrid 算力合作社",
            menu,
        )
        return icon
    except Exception as e:
        logger.warning("系统托盘初始化失败: %s", e)
        return None


def main():
    """桌面版主入口。"""
    PORT = 8000

    # 设置日志
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("=" * 50)
    print("  Cogrid 桌面版启动中...")
    print("=" * 50)

    # 1. 启动协调器
    print("[1/4] 启动协调器...", end="", flush=True)
    coord_thread = start_coordinator(port=PORT)
    print(" OK")

    # 2. 等待就绪
    print("[2/4] 等待协调器就绪...", end="", flush=True)
    if not wait_for_coordinator(port=PORT):
        print(" FAILED")
        print("协调器启动失败，请检查端口是否被占用。")
        input("按回车键退出...")
        sys.exit(1)
    print(" OK")

    # 3. 启动本地 Agent（可选）
    print("[3/4] 启动本地 Agent...", end="", flush=True)
    agent_thread = start_agent(port=PORT)
    if agent_thread:
        print(" OK")
    else:
        print(" 跳过")

    # 4. 打开浏览器 + 托盘
    print("[4/4] 打开仪表盘...", end="", flush=True)
    webbrowser.open(f"http://127.0.0.1:{PORT}")
    print(" OK")

    print()
    print(f"  仪表盘: http://127.0.0.1:{PORT}")
    print(f"  API 文档: http://127.0.0.1:{PORT}/docs")
    print()
    print("  关闭窗口或点击托盘图标退出。")
    print("=" * 50)

    # 启动系统托盘（阻塞主线程）
    icon = create_tray_icon(port=PORT)
    if icon:
        icon.run()
    else:
        # 无托盘时，等待用户输入退出
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n退出...")


if __name__ == "__main__":
    main()
