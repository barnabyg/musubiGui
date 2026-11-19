@echo off
setlocal
cd /d "%~dp0"
set "UV_CACHE_DIR=%CD%\.cache\uv"
set "UV_PYTHON_PREFERENCE=only-system"
set "MUSUBI_LAUNCH_PYTHON="
for /f "delims=" %%P in ('uv python find --system --no-python-downloads 3.12') do set "MUSUBI_LAUNCH_PYTHON=%%P"
if not defined MUSUBI_LAUNCH_PYTHON exit /b 1
"%MUSUBI_LAUNCH_PYTHON%" -m musubi_gui.launcher %*
