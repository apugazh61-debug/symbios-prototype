@echo off
title SYMBIOS - Industrial Safety Platform
echo ======================================================================
echo          Starting SYMBIOS Industrial Safety Platform Demo
echo ======================================================================
echo.

cd /d "%~dp0"

if exist "venv\Scripts\activate.bat" (
    echo [OK] Activating virtual environment...
    call venv\Scripts\activate.bat
) else (
    echo [WARNING] venv not found, using global Python.
)

echo [INFO] Launching SYMBIOS Backend Server on http://127.0.0.1:8000 ...
start "SYMBIOS Backend" /b uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload

echo [INFO] Waiting for backend to initialize...
timeout /t 3 /nobreak >nul

echo [INFO] Opening Frontend Dashboard in browser...
start "" "frontend\index.html"

echo.
echo ======================================================================
echo  Backend running at : http://127.0.0.1:8000
echo  API Documentation  : http://127.0.0.1:8000/docs
echo  Dashboard UI       : frontend\index.html
echo ======================================================================
echo Press Ctrl+C or close this window to stop the server.
echo.
pause
