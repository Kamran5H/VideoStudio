@echo off
title VideoStudio Pro — 4K AI Video Studio
cd /d "%~dp0"

echo ================================================================
echo   VideoStudio Pro — 4K AI Video Studio (Obsidian Cinema)
echo   Crafted with precision for Kamran Ashraf (Kami)
echo ================================================================

rem --- Clean any stale worker locks from previous crashed sessions ---
if exist "%~dp0outputs\queue\worker.lock" del /f /q "%~dp0outputs\queue\worker.lock" >nul 2>&1

rem --- Multi-candidate Python Environment Resolution ---
set "PY="
if exist "%~dp0..\.venv\python.exe" set "PY=%~dp0..\.venv\python.exe"
if not defined PY if exist "%~dp0..\.venv\Scripts\python.exe" set "PY=%~dp0..\.venv\Scripts\python.exe"
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "%~dp0.venv\python.exe" set "PY=%~dp0.venv\python.exe"
if not defined PY set "PY=python"

echo Using Python runtime: %PY%
echo.

rem --- Check if VideoStudio server is already running on 7860 ---
powershell -NoProfile -Command "$client = New-Object System.Net.Sockets.TcpClient; try { $client.Connect('127.0.0.1', 7860); Write-Output 'UP' } catch { } finally { $client.Dispose() }" 2>nul | findstr /i "UP" >nul
if not errorlevel 1 (
    echo VideoStudio is already running - opening browser...
    start "" "http://127.0.0.1:7860/"
    exit /b
)

echo Starting VideoStudio Pro... the browser opens automatically when ready.
echo Keep this window open while using the studio; close it to stop the server.
echo.

"%PY%" app.py

echo.
echo ================================================================
echo  VideoStudio stopped.
echo ================================================================
pause
