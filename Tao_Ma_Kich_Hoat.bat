@echo off
cd /d "D:\Antigravity_Projects\Telegram Toolkit Pro"
start "" "%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe" admin_keygen.py
if errorlevel 1 (
    start "" pythonw admin_keygen.py
)
exit
