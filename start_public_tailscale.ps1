$ErrorActionPreference = "Stop"

$mk5Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$tailscaleCommand = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $tailscaleCommand) {
    $installedTailscale = "C:\Program Files\Tailscale\tailscale.exe"
    if (Test-Path -LiteralPath $installedTailscale) {
        $tailscaleCommand = Get-Item -LiteralPath $installedTailscale
    }
}

if (-not $pythonCommand) {
    throw "python 명령을 찾지 못했습니다. MK5 가상환경을 활성화한 뒤 다시 실행하세요."
}
if (-not $tailscaleCommand) {
    throw "tailscale 명령을 찾지 못했습니다. Tailscale을 설치하고 로그인하세요."
}

$tailscaleExe = $tailscaleCommand.Path
if (-not $tailscaleExe) {
    $tailscaleExe = $tailscaleCommand.Source
}
if (-not $tailscaleExe) {
    $tailscaleExe = $tailscaleCommand.FullName
}
if (-not $tailscaleExe) {
    throw "Tailscale 실행 파일 경로를 확인하지 못했습니다."
}

$env:MK5_SERVER_HOST = "127.0.0.1"
$env:MK5_SERVER_PORT = "8010"
$env:MK5_SESSION_COOKIE_SECURE = "true"

function Test-Mk5Health {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8010/health" -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

$funnelStatus = (& $tailscaleExe funnel status 2>&1) -join "`n"
$expectedProxy = "proxy http://127.0.0.1:8010"
if ($funnelStatus -notmatch [regex]::Escape($expectedProxy)) {
    & $tailscaleExe funnel --bg 8010
    if ($LASTEXITCODE -ne 0) {
        throw "Tailscale Funnel 설정에 실패했습니다. 기존 Serve/Funnel 설정을 확인하세요."
    }
    $funnelStatus = (& $tailscaleExe funnel status 2>&1) -join "`n"
}

if (Test-Mk5Health) {
    Write-Host "이미 실행 중인 MK5 서버를 사용합니다."
    Write-Host $funnelStatus
    return
}

Write-Host $funnelStatus
Write-Host "MK5 서버를 시작합니다. 종료하려면 Ctrl+C를 누르세요."
Push-Location $mk5Root
try {
    & $pythonCommand.Source (Join-Path $mk5Root "run_server.py")
}
finally {
    Pop-Location
}
