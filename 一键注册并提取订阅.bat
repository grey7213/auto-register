@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=D:\Anconda3\python.exe"

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
