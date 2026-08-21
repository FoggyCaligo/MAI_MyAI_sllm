@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_public_tailscale.ps1" %*
if errorlevel 1 exit /b %errorlevel%
