@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=D:\Anconda3\python.exe"

echo ============================================
echo   干跑检测（不改 Clash、不注册）
echo ============================================
"%PY%" auto_switch.py --once --dry-run
echo.
pause
