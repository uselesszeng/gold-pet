@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist .venv (
  python -m pip install -r requirements.txt
)

python -m pip install pyinstaller

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name 黄金桌宠 ^
  --icon assets\icon.ico ^
  --add-data "assets;assets" ^
  run.py

echo.
echo 打包完成：dist\黄金桌宠.exe
pause
