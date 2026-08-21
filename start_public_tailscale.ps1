$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Get-Command python -ErrorAction SilentlyContinue
$tailscale = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $tailscale) {
    $installed = "C:\Program Files\Tailscale\tailscale.exe"
    if (Test-Path -LiteralPath $installed) {
        $tailscale = Get-Item -LiteralPath $installed
    }
}

if (-not $python) {
    throw "python 명령을 찾지 못했습니다. 가상환경을 활성화한 뒤 다시 실행하세요."
}
if (-not $tailscale) {
    throw "tailscale 명령을 찾지 못했습니다. Tailscale을 설치하고 로그인하세요."
}

$tailscaleExe = $tailscale.Path
if (-not $tailscaleExe) { $tailscaleExe = $tailscale.Source }
if (-not $tailscaleExe) { $tailscaleExe = $tailscale.FullName }
if (-not $tailscaleExe) { throw "Tailscale 실행 파일 경로를 확인하지 못했습니다." }

$port = 8010
if ($env:MAI_SERVER_PORT) {
    $port = [int]$env:MAI_SERVER_PORT
}

$env:MAI_SERVER_HOST = "127.0.0.1"
$env:MAI_SERVER_PORT = [string]$port
$env:MAI_SESSION_COOKIE_SECURE = "true"

Write-Host "[MAI] tailscale | funnel port=$port"
& $tailscaleExe funnel --bg $port
if ($LASTEXITCODE -ne 0) {
    throw "Tailscale Funnel 설정에 실패했습니다. tailscale funnel status를 확인하세요."
}

Write-Host "[MAI] tailscale | public hosting ready"
& $tailscaleExe funnel status

Push-Location $root
try {
    & $python.Source (Join-Path $root "run_server.py")
}
finally {
    Pop-Location
}
