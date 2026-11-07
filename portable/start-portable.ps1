# Single-GGUF portable MAI. llama.cpp engine is replaceable independently of GGUF.
param(
    [string]$GgufPath
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root 'portable/portable-runtimes/python/python.exe'
$llama = Join-Path $root 'portable/portable-runtimes/llamacpp/llama-server.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Portable Python is missing: $python" }
if (-not (Test-Path -LiteralPath $llama -PathType Leaf)) { throw "Portable llama.cpp server is missing: $llama" }
if (-not (Test-Path -LiteralPath (Join-Path $root '.env') -PathType Leaf)) {
    throw 'Missing .env; create it from .env.example and configure credentials.'
}

if (-not $GgufPath) { $GgufPath = Join-Path $root 'portable/portable-models/gguf/active.gguf' }
$modelFile = (Resolve-Path -LiteralPath $GgufPath -ErrorAction Stop).Path
if (-not [IO.Path]::GetExtension($modelFile).Equals('.gguf', [StringComparison]::OrdinalIgnoreCase)) {
    throw "Single portable model must be a .gguf file: $modelFile"
}
$modelAlias = & $python -c 'import pathlib,yaml; p=pathlib.Path("settings.yaml"); d=yaml.safe_load(p.read_text(encoding="utf-8-sig")); print(d["MAIN_MODEL"])'
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($modelAlias)) { throw 'Invalid settings.yaml MAIN_MODEL alias' }
$modelAlias = $modelAlias.Trim()

$env:PYTHONNOUSERSITE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $root 'portable/portable-runtimes/playwright-browsers'
$env:MAI_CWD = $root
$env:MAI_UPLOAD_ROOT = Join-Path $root 'mai_uploads'
$env:MEMORY_DB_PATH = Join-Path $root 'data/memory.sqlite3'
$env:SENTENCE_BREAKER_DB_PATH = Join-Path $root 'data/sentence_breaker.sqlite3'
$env:CHAT_DB_PATH = Join-Path $root 'data/chat.sqlite3'
$env:LOG_DB_PATH = Join-Path $root 'data/log.sqlite3'
$env:MAIN_MODEL = $modelAlias
$env:LLM_BACKEND = 'llamacpp'
$env:LLAMACPP_HOST = 'http://127.0.0.1:8080'
$env:MAI_SLOT_CACHE_DIR = Join-Path $root 'portable/portable-slot-cache'
New-Item -ItemType Directory -Force -Path $env:MAI_SLOT_CACHE_DIR | Out-Null
# Ignore obsolete multi-model environment overrides from earlier MAI builds.
Remove-Item Env:VERIFIER_MODEL,Env:VISION_MODEL,Env:OLLAMA_HOST,Env:OLLAMA_MODELS -ErrorAction SilentlyContinue

if (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 8080 is occupied. Stop the old llama.cpp server before launching portable MAI.'
}
$serverArgs = @('-m', $modelFile, '--alias', $modelAlias,
                '--host', '127.0.0.1', '--port', '8080', '--jinja',
                '--parallel', '1', '--slot-save-path', $env:MAI_SLOT_CACHE_DIR)
$llamaProcess = Start-Process -FilePath $llama -ArgumentList $serverArgs -WorkingDirectory (Split-Path $llama) -PassThru -NoNewWindow
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 100; $attempt++) {
        if ($llamaProcess.HasExited) { throw "llama.cpp exited with code $($llamaProcess.ExitCode)" }
        try {
            $response = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/v1/models' -TimeoutSec 2
            if ($response.data.Count -eq 1 -and $response.data[0].id -eq $modelAlias) {
                $ready = $true
                break
            }
        } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw 'llama.cpp did not expose the selected single-model alias' }
    & $python run_server.py
    if ($LASTEXITCODE -ne 0) { throw "MAI exited with code $LASTEXITCODE" }
} finally {
    if (-not $llamaProcess.HasExited) { Stop-Process -Id $llamaProcess.Id }
}
