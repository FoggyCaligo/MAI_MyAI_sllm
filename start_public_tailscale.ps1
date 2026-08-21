param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$tailscale = Get-Command tailscale -ErrorAction Stop
$python = Get-Command python -ErrorAction Stop

Write-Host "Starting MK5 on http://127.0.0.1:$Port"
$env:MAI_HOST = "127.0.0.1"
$env:MAI_PORT = "$Port"

$server = Start-Process -FilePath $python.Source -ArgumentList @("run_server.py") -PassThru -NoNewWindow
try {
    Start-Sleep -Seconds 2
    if ($server.HasExited) {
        throw "MK5 server exited before Tailscale Funnel started."
    }

    Write-Host "Configuring Tailscale Funnel..."
    & $tailscale.Source funnel --bg --yes "http://127.0.0.1:$Port"
    if ($LASTEXITCODE -ne 0) {
        throw "tailscale funnel failed with exit code $LASTEXITCODE"
    }

    & $tailscale.Source funnel status
    Write-Host "MK5 server PID: $($server.Id)"
    Write-Host "Stop the Python process explicitly when you want to shut the server down."
}
catch {
    if (-not $server.HasExited) {
        Stop-Process -Id $server.Id -Force
    }
    throw
}
