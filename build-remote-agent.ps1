# Build a standalone Windows executable for the explicitly started MAI Remote Agent.
# Build-time Python/uv may be installed on the preparation PC; target PC does not require Python.
param([string]$Python = "python")
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
& $Python -m pip install --upgrade pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller dependency installation failed" }
& $Python -m PyInstaller --clean --onefile --name mai-remote-agent --paths $root (Join-Path $root 'mai\remote\agent.py')
if ($LASTEXITCODE -ne 0) { throw "Remote Agent packaging failed" }
Write-Host (Join-Path $root 'dist\mai-remote-agent.exe')
