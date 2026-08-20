@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_public_tailscale.ps1"
set "MK5_EXIT_CODE=%ERRORLEVEL%"
if not "%MK5_EXIT_CODE%"=="0" pause
exit /b %MK5_EXIT_CODE%
