@echo off
title AI Video Studio
cd /d "D:\VideoStudio"
echo Starting AI Video Studio... your browser will open shortly.
python app.py
if errorlevel 1 (
    echo.
    echo Something went wrong. Read the error above, then press any key to close.
    pause >nul
)
