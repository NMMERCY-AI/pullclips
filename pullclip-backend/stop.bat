@echo off

echo Stopping frontend (Node.js)...
taskkill /F /IM node.exe >nul 2>&1

echo Stopping backend (Python/Uvicorn)...
taskkill /F /IM python.exe >nul 2>&1

echo All services stopped.
pause