param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$tailscale = Get-Command tailscale -ErrorAction Stop
$python = Get-Command python -ErrorAction Stop

$env:MAI_HOST = "127.0.0.1"
$env:MAI_PORT = "$Port"

Write-Host "Checking Tailscale connection..."
& $tailscale.Source status
if ($LASTEXITCODE -ne 0) {
    throw "tailscale status failed with exit code $LASTEXITCODE"
}

Write-Host "Configuring Tailscale Funnel for http://127.0.0.1:$Port ..."
& $tailscale.Source funnel --bg --yes "http://127.0.0.1:$Port"
if ($LASTEXITCODE -ne 0) {
    throw "tailscale funnel failed with exit code $LASTEXITCODE"
}

& $tailscale.Source funnel status
Write-Host "Starting Mai on http://127.0.0.1:$Port"
Write-Host "Mai stays attached to this terminal. Press Ctrl+C here to stop the Python server gracefully."

& $python.Source "run_server.py"
$serverExitCode = $LASTEXITCODE
if ($serverExitCode -ne 0) {
    throw "Mai server exited with code $serverExitCode"
}
