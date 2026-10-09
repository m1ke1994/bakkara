@echo off
setlocal
cd /d "%~dp0"
if not exist .env copy .env.example .env >nul
start "BAKKARA Backend" cmd /k "cd /d %~dp0backend && python -m pip install -r requirements.txt && python -m playwright install chromium && python -m uvicorn app.main:app --host 127.0.0.1 --port 8010"
start "BAKKARA Frontend" cmd /k "cd /d %~dp0frontend && npm install && npm run dev"
endlocal
