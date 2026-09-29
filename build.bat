@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist .venv (
  python -m pip install -r requirements.txt
)
python -m pip install pyinstaller

python -m PyInstaller --noconfirm --clean gold-pet.spec

echo.
echo 打包完成：dist\黄金桌宠.exe
echo 调试模式（只打印一次行情，不弹窗）：dist\黄金桌宠.exe --once
pause
