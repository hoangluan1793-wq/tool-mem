@echo off
chcp 65001 >nul
title Dong Goi Ban Cho Khach
echo ============================================================
echo   DANG DONG GOI BAN CHO KHACH (CHONG LO MA NGUON CODE)...
echo ============================================================
echo.
"%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" Dong_Goi_Ban_Cho_Khach.py
if errorlevel 1 (
    python Dong_Goi_Ban_Cho_Khach.py
)
echo.
pause
