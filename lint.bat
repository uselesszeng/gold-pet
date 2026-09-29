@echo off
chcp 65001 >nul
cd /d "%~dp0"

python -m ruff check .
python -m ruff format .
python -m mypy
python -m pytest tests --cov=gold_pet --cov-report=term-missing

echo.
echo 质量检查完成
pause
