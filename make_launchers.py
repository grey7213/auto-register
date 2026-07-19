# -*- coding: utf-8 -*-
"""Generate double-clickable .bat launchers with correct GBK + CRLF encoding.

Run once with your Python:  python make_launchers.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

# Prefer the interpreter that runs this script.
PYEXE = sys.executable or r"python"


REGISTER_AND_EXPORT = f'''@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY={PYEXE}"

echo ============================================
echo   [1/2] 正在注册新账号（随机邮箱/IP）...
echo ============================================
"%PY%" register_account.py --base-url https://ssyun.org --count 1 --output accounts.jsonl
if errorlevel 1 (
    echo.
    echo [错误] 注册失败，已停止。
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   [2/2] 正在提取新号订阅并生成配置...
echo ============================================
"%PY%" export_subscription.py --latest

echo.
echo ============================================
echo   完成！配置文件在 clash_profiles 文件夹里。
echo   订阅链接见上方输出。
echo ============================================
echo.
pause
'''

EXPORT_ALL = f'''@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY={PYEXE}"

echo ============================================
echo   正在提取所有账号的订阅并生成配置...
echo ============================================
"%PY%" export_subscription.py

echo.
echo ============================================
echo   完成！配置文件在 clash_profiles 文件夹里。
echo ============================================
echo.
pause
'''

REGISTER_GUI = f'''@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY={PYEXE}"
start "" "%PY%" register_gui.py
'''

AUTO_SWITCH_ONCE = f'''@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY={PYEXE}"

echo ============================================
echo   检测 2G 订阅流量并自动切换 / 注册
echo ============================================
"%PY%" auto_switch.py --once
echo.
pause
'''

AUTO_SWITCH_LOOP = f'''@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY={PYEXE}"

echo ============================================
echo   常驻监控：用完 2G 自动切下一个 / 自动注册
echo   关闭本窗口即停止监控
echo   默认每 120 秒检测一次
echo ============================================
"%PY%" auto_switch.py --interval 120
echo.
pause
'''

AUTO_SWITCH_DRY = f'''@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY={PYEXE}"

echo ============================================
echo   干跑检测（不改 Clash、不注册）
echo ============================================
"%PY%" auto_switch.py --once --dry-run
echo.
pause
'''

LAUNCHERS = {
    "一键注册并提取订阅.bat": REGISTER_AND_EXPORT,
    "提取所有订阅.bat": EXPORT_ALL,
    "打开注册机GUI.bat": REGISTER_GUI,
    "自动切换-检测一次.bat": AUTO_SWITCH_ONCE,
    "自动切换-常驻监控.bat": AUTO_SWITCH_LOOP,
    "自动切换-干跑检测.bat": AUTO_SWITCH_DRY,
}


def main() -> None:
    here = Path(__file__).resolve().parent
    for name, content in LAUNCHERS.items():
        crlf = content.replace("\n", "\r\n")
        path = here / name
        # cmd on zh-CN Windows often reads bat as system ANSI (GBK)
        try:
            path.write_bytes(crlf.encode("gbk"))
        except UnicodeEncodeError:
            path.write_bytes(crlf.encode("utf-8"))
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
