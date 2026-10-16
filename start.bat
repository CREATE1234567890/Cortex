@echo off
chcp 65001 >nul
title Cortex - Personal Research KB QA Agent
cd /d "%~dp0"

echo [Cortex] Starting backend (port 8300)...
start "Cortex-Backend" cmd /k "cd backend && set PYTHONUTF8=1 && python -m uvicorn app.main:app --host 127.0.0.1 --port 8300"

echo [Cortex] Starting frontend (port 3000)...
start "Cortex-Frontend" cmd /k "cd frontend && set NEXT_TELEMETRY_DISABLED=1 && npm run dev"

timeout /t 8 /nobreak >nul
start http://localhost:3000

echo.
echo Cortex is running:
echo   Web UI  : http://localhost:3000
echo   API     : http://127.0.0.1:8300/docs
echo Close the two popped-up windows to stop.
pause
