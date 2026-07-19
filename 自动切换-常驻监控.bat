@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=D:\Anconda3\python.exe"

echo ============================================
echo   常驻监控：用完 2G 自动切下一个 / 自动注册
echo   关闭本窗口即停止监控
echo   默认每 120 秒检测一次
echo ============================================
"%PY%" auto_switch.py --interval 120
echo.
pause
