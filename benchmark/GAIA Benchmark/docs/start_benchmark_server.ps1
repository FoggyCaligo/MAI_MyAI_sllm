# 평가 전용 MAI 서버: 실제 사용자 DB를 사용하지 않도록 별도 경로 지정
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
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
Write-Host "MAI benchmark server: http://127.0.0.1:8010"
Write-Host "DB directory: $data"
Push-Location $project
try {
    & $python 'run_server.py'
    if ($LASTEXITCODE -ne 0) { throw "벤치마크 MAI 서버 종료 오류: $LASTEXITCODE" }
} finally {
    Pop-Location
}
