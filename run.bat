@echo off
chcp 65001 >nul
title Telegram Toolkit Pro

echo ======================================================
echo           TELEGRAM TOOLKIT PRO (PRO EDITION)
echo ======================================================
echo.

python -c "print(1)" >nul 2>&1
if errorlevel 1 (
    echo [THONG BAO] May tinh cua ban CHUA CAI DAT PYTHON!
    pause
    exit /b
)

echo [*] Dang khoi dong ung dung Telegram Toolkit Pro...
python app.py

pause