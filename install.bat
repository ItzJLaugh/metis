@echo off
rem Double-click to install Metis (see install.ps1 for what it does).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
