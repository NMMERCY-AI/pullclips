@echo off

echo Starting frontend...

cd /d "%~dp0..\pullclip"

if not exist node_modules (
    echo Installing frontend dependencies...
    call npm install
)

start "Frontend" cmd /k "npm run dev"

echo Starting backend...

cd /d "%~dp0"

start "Backend" cmd /k "uvicorn main:app --host 0.0.0.0 --port 8000"

echo Done!
pause