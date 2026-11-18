@echo off
chcp 65001 >nul
title Tao Ban Cap Nhat
cd /d "%~dp0"
"%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" Tao_Ban_Cap_Nhat.py %*
if errorlevel 9009 (
    python Tao_Ban_Cap_Nhat.py %*
)
echo.
pause
