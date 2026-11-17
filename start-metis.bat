@echo off
rem Opens the Metis desktop app. Run install.bat first.
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo Metis is not installed yet. Double-click install.bat first.
  pause
  exit /b 1
)
cd /d "%~dp0connector"
start "" "%~dp0.venv\Scripts\pythonw.exe" -m metis_mcp.app
