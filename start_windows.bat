@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Installing local Python environment...
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1"
  if errorlevel 1 (
    echo Windows setup failed.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -m dialogue_tool desktop
if errorlevel 1 (
  echo App failed to launch. Run .venv\Scripts\python.exe -m dialogue_tool doctor
  pause
  exit /b 1
)
endlocal
