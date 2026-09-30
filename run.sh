#!/usr/bin/env bash
# 黄金桌宠 —— macOS / Linux 一键启动脚本
#
# 用法：
#   ./run.sh              正常启动
#   ./run.sh --once       只打印一次行情，不开界面
#   ./run.sh --log-level DEBUG
#
# 首次运行会自动创建 .venv 虚拟环境并安装依赖（约几十 MB，需联网）。

set -e

cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

# 1. 检查 Python 是否可用
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "❌ 找不到 python3。请先安装 Python 3.11 或更高版本。"
  echo "   macOS : brew install python@3.12   或到 https://www.python.org/downloads/ 下载"
  echo "   Ubuntu: sudo apt install -y python3 python3-pip python3-venv"
  exit 1
fi

# 2. 首次运行：建虚拟环境 + 装依赖
if [ ! -d .venv ]; then
  echo "📦 首次运行，正在创建虚拟环境…"
  "$PY" -m venv .venv
  echo "📥 正在安装依赖（需要联网，请稍候）…"
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt
  echo "✅ 依赖安装完成"
fi

# 3. 启动
exec ./.venv/bin/python run.py "$@"
