@echo off
chcp 65001 >nul
REM ============================================================
REM  Cogrid 桌面版 Windows 构建脚本
REM  生成 dist\cogrid.exe 一体化桌面应用
REM ============================================================

echo ============================================================
echo   Cogrid 桌面版构建
echo ============================================================
echo.

REM ---- 1. 检查 Python ----
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 未找到 Python，请先安装 Python 3.11+
    pause
    exit /b 1
)

REM ---- 2. 安装依赖 ----
echo [1/5] 安装 Python 依赖...
pip install pyinstaller pystray pillow --quiet
pip install -e coordinator/ -e agent/ -e cli/ --quiet
if errorlevel 1 (
    echo [ERROR] 依赖安装失败
    pause
    exit /b 1
)
echo       OK
echo.

REM ---- 3. 构建仪表盘静态文件 ----
echo [2/5] 构建仪表盘...
where npm >nul 2>&1
if errorlevel 1 (
    echo [WARN] 未找到 npm，跳过仪表盘构建
    echo       仪表盘将通过 API 代理访问
) else (
    cd dashboard
    npm install --silent 2>nul
    npm run build
    cd ..
    echo       OK
)
echo.

REM ---- 4. 生成图标（如果没有） ----
if not exist desktop\cogrid.ico (
    echo [3/5] 生成应用图标...
    python -c "from PIL import Image, ImageDraw; img=Image.new('RGB',(256,256),(30,30,46)); d=ImageDraw.Draw(img); d.ellipse([56,56,200,200],fill=(52,211,153)); d.text((96,108),'CG',fill=(30,30,46)); img.save('desktop/cogrid.ico')"
    echo       OK
) else (
    echo [3/5] 图标已存在，跳过
)
echo.

REM ---- 5. PyInstaller 打包 ----
echo [4/5] PyInstaller 打包中（可能需要几分钟）...
pyinstaller desktop/cogrid.spec --noconfirm --clean
if errorlevel 1 (
    echo [ERROR] 打包失败
    pause
    exit /b 1
)
echo       OK
echo.

REM ---- 6. 完成 ----
echo [5/5] 构建完成！
echo.
echo   输出文件: dist\cogrid.exe
echo   文件大小:
for %%A in (dist\cogrid.exe) do echo   %%~zA bytes
echo.
echo   使用方法: 双击 cogrid.exe 启动
echo   仪表盘: http://127.0.0.1:8000
echo.
pause
