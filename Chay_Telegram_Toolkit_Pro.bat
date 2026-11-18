@echo off
cd /d "D:\Antigravity_Projects\Telegram Toolkit Pro"
start "" "%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe" app.py
if errorlevel 1 (
    start "" pythonw app.py
)
exit
