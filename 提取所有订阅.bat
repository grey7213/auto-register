@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=D:\Anconda3\python.exe"

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
