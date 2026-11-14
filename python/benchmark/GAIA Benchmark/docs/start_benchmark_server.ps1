# 평가 전용 MAI 서버: router 1개 + MAI 평가 서버 1개.
param([switch]$ServerOnly)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
if (-not $ServerOnly) {
    $routerLauncher = Join-Path $project 'portable/start-portable-llamacpp-router.ps1'
    if (-not (Test-Path -LiteralPath $routerLauncher -PathType Leaf)) {
        throw "MAI Portable router launcher missing: $routerLauncher"
    }
    & $routerLauncher -GaiaBenchmark
    return
}
$data = Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..')).Path 'result\_runtime'
New-Item -ItemType Directory -Force -Path $data | Out-Null
$env:MEMORY_DB_PATH = Join-Path $data 'memory.sqlite3'
$env:SENTENCE_BREAKER_DB_PATH = Join-Path $data 'sentence_breaker.sqlite3'
$env:CHAT_DB_PATH = Join-Path $data 'chat.sqlite3'
$env:LOG_DB_PATH = Join-Path $data 'log.sqlite3'
$env:REMOTE_HOSTING_PROVIDER = 'none'
$env:MAI_HOST = '127.0.0.1'
$env:MAI_PORT = '8010'
$env:MAI_VERIFIER_ENABLED = 'false'
$python = Join-Path $project 'portable\portable-runtimes\python\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "MAI Portable Python 실행 파일 없음: $python"
}
$resolver = Join-Path $project 'benchmark\GAIA Benchmark\adapter\resolve_main_model.py'
$model = & $python $resolver --root $project
if ($LASTEXITCODE -ne 0) { throw 'GAIA main model resolution failed' }
if (@($model).Count -ne 1 -or [string]::IsNullOrWhiteSpace([string]$model)) { throw 'GAIA main model resolver did not return exactly one model ID' }
$env:MAIN_MODEL = [string]$model
Write-Host "MAI benchmark model: $($env:MAIN_MODEL)"
Write-Host "MAI benchmark server: http://127.0.0.1:8010"
Write-Host "DB directory: $data"
Push-Location $project
try {
    & $python 'run_server.py'
    if ($LASTEXITCODE -ne 0) { throw "벤치마크 MAI 서버 종료 오류: $LASTEXITCODE" }
} finally {
    Pop-Location
}
