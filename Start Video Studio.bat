@echo off
title VideoStudio Pro - 4K AI Video ^& Storyboard Studio
cd /d "C:\Users\chkam\OneDrive\Desktop\BrandFinder\VideoStudio"

echo ================================================================
echo   VideoStudio Pro - 4K AI Video ^& Storyboard Studio
echo   Crafted by Kamran Ashraf (Kami)
echo ================================================================

rem --- pick a real Python (no PATH guesswork) ---
set "PY=C:\Users\chkam\AppData\Local\Programs\Python\Python314\python.exe"
if not exist "%PY%" set "PY=python"

rem --- already running? just open the browser and exit ---
curl -s -o nul -m 2 http://127.0.0.1:7860/ >nul 2>&1
if not errorlevel 1 (
    echo VideoStudio is already running - opening browser...
    start "" "http://127.0.0.1:7860/"
    exit /b
)

echo.
echo Starting VideoStudio Pro... the browser opens automatically when ready.
echo (First launch can take 20-40 seconds while AI libraries load.)
echo Keep this window open while you use the studio; close it to stop the server.
echo.

"%PY%" app.py

echo.
echo ================================================================
echo  VideoStudio stopped. If it closed because of an error, the
echo  message is shown above. Press any key to close this window.
echo ================================================================
pause >nul
