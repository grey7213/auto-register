#!/usr/bin/env bash
# Double-click / open-with-terminal launcher for the registration GUI.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if command -v python3 >/dev/null 2>&1; then
  exec python3 register_gui.py
fi

echo "未找到 python3，无法启动注册机。"
read -r -p "按回车键退出…"
exit 1
