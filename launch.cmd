@echo off
setlocal
cd /d "%~dp0"
set "UV_CACHE_DIR=%CD%\.cache\uv"
set "UV_PYTHON_PREFERENCE=only-system"
call uv sync --locked
if errorlevel 1 exit /b 1
call npm.cmd ci --cache .cache/npm
if errorlevel 1 exit /b 1
call npm.cmd run build
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m musubi_gui %*
