@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=D:\Anconda3\python.exe"

echo ============================================
echo   检测 2G 订阅流量并自动切换 / 注册
echo ============================================
"%PY%" auto_switch.py --once
echo.
pause
