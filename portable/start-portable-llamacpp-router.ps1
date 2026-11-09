# Portable llama.cpp router launcher for MAI.
# One verified GGUF catalog and at most one resident model.
param(
    [ValidateRange(1,1)][int]$ModelsMax = 1,
    [ValidateRange(0,999)][int]$GpuLayers = 99,
    [ValidateRange(512,262144)][int]$ContextSize = 131072,
    [switch]$GaiaBenchmark
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root 'portable/portable-runtimes/python/python.exe'
$server = Join-Path $root 'portable/portable-runtimes/llamacpp/llama-server.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Portable Python missing: $python" }
if (-not (Test-Path -LiteralPath $server -PathType Leaf)) { throw "llama-server missing: $server" }
if (-not (Test-Path -LiteralPath (Join-Path $root '.env') -PathType Leaf)) { throw 'Missing .env' }
# The catalog is generated from verified GGUF files on every launcher start.
$iniDir = Join-Path $root 'portable/portable-runtimes/llamacpp'
$ini = Join-Path $iniDir 'mai-models.generated.ini'
$catalogJson = & $python (Join-Path $root 'portable/llamacpp_model_catalog.py') --root $root --ini $ini --context-size $ContextSize --gpu-layers $GpuLayers
if ($LASTEXITCODE -ne 0) { throw 'Could not discover GGUF models and create router preset' }
$catalog = $catalogJson | ConvertFrom-Json
$defaultModel = [string]$catalog.default
if (-not $defaultModel) { throw 'GGUF catalog has no default model' }
$MainVision = [bool]$catalog.main_vision
$mainProjector = [string]$catalog.projector
# The launcher injects this option into both the router and spawned model
# processes. Keep the policy in settings.yaml, not in the downloaded binary.
$preserveReasoning = [bool]$catalog.reasoning_preserve
$env:LLAMA_ARG_REASONING_PRESERVE = if ($preserveReasoning) { 'true' } else { 'false' }
if (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 8080 occupied; refusing to connect to another router.'
}
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:MAI_CWD = $root
$env:MAI_UPLOAD_ROOT = Join-Path $root 'mai_uploads'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $root 'portable/portable-runtimes/playwright-browsers'
$env:MEMORY_DB_PATH = Join-Path $root 'MAI_MEMORY/memory.sqlite3'
$env:SENTENCE_BREAKER_DB_PATH = Join-Path $root 'MAI_MEMORY/sentence_breaker.sqlite3'
$env:CHAT_DB_PATH = Join-Path $root 'MAI_MEMORY/chat.sqlite3'
$env:LOG_DB_PATH = Join-Path $root 'MAI_MEMORY/log.sqlite3'
$env:VOICE_PROFILE_DIR = Join-Path $root 'MAI_MEMORY/voice_profiles'
$env:LLM_BACKEND = 'llamacpp'
$env:MAIN_MODEL = $defaultModel
$env:LLAMACPP_HOST = 'http://127.0.0.1:8080'
$env:LLAMACPP_MAIN_VISION = if ($mainProjector -or $MainVision) { 'true' } else { 'false' }
# Start-Process ArgumentList needs quoted file paths.
$arguments = @('--models-preset', ('"' + $ini + '"'), '--models-max', "$ModelsMax",
               '--host', '127.0.0.1', '--port', '8080', '--jinja')
if ($preserveReasoning) { $arguments += '--reasoning-preserve' } else { $arguments += '--no-reasoning-preserve' }
$process = $null
try {
    $process = Start-Process -FilePath $server -ArgumentList $arguments -WorkingDirectory $iniDir -PassThru -NoNewWindow
    $ready = $false
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        if ($process.HasExited) { throw "llama.cpp router exited with $($process.ExitCode)" }
        try {
            $listing = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/v1/models' -TimeoutSec 2
            $ids = @($listing.data | ForEach-Object { $_.id })
            if ($ids -contains $defaultModel -and @($catalog.models | Where-Object { $ids -notcontains $_.id }).Count -eq 0) { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw 'Router model aliases not registered within startup deadline' }
    Write-Host 'MAI llama.cpp router ready on 127.0.0.1:8080; '
    Write-Host 'MAI Portable single-resident mode: max one GGUF loaded by llama.cpp router.'
    if ($GaiaBenchmark) {
        $benchmarkLauncher = Join-Path $root 'benchmark/GAIA Benchmark/docs/start_benchmark_server.ps1'
        if (-not (Test-Path -LiteralPath $benchmarkLauncher -PathType Leaf)) {
            throw "GAIA benchmark server launcher missing: $benchmarkLauncher"
        }
        & $benchmarkLauncher -ServerOnly
    } else {
        & $python run_server.py
        if ($LASTEXITCODE -ne 0) { throw "MAI exited with code $LASTEXITCODE" }
    }
} finally {
    if ($null -ne $process -and -not $process.HasExited) {
        # A router can spawn children; terminate the entire process tree.
        & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
    }
}
