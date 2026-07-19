@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=D:\Anconda3\python.exe"
start "" "%PY%" register_gui.py
