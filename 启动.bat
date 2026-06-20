@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo   夸克分享码管理系统
echo ========================================
echo.
call ..\..\venv\Scripts\activate.bat >nul 2>&1
python main.py
pause
