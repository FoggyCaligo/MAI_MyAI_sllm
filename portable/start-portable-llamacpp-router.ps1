# Portable llama.cpp router launcher for MAI.
# One verified GGUF catalog and at most one resident model.
param(
    [ValidateRange(1,1)][int]$ModelsMax = 1,
    [ValidateRange(0,999)][int]$GpuLayers = 99,
    [ValidateRange(0,262144)][int]$ContextSize = 0,
    [switch]$GaiaBenchmark,
    [switch]$RouterOnly
)
$ErrorActionPreference = 'Stop'
if ($GaiaBenchmark -and $RouterOnly) { throw 'GaiaBenchmark and RouterOnly cannot be combined' }
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
$catalogArgs = @((Join-Path $root 'portable/llamacpp_model_catalog.py'), '--root', $root, '--ini', $ini, '--gpu-layers', "$GpuLayers")
if ($ContextSize -ne 0) { $catalogArgs += @('--context-size', "$ContextSize") }
$catalogJson = & $python @catalogArgs
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
# Applied to the router and its spawned llama.cpp model process.
$env:LLAMA_ARG_CACHE_RAM = [string]$catalog.cache_ram_mib
foreach ($port in @(8080, 8081)) {
    if (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port occupied; refusing to connect to another router."
    }
}
& $python -m portable.runtime_data_paths --root $root
if ($LASTEXITCODE -ne 0) { throw 'MAI runtime data migration blocked: check old/new data folders before starting.' }
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:MAI_CWD = $root
$env:MAI_UPLOAD_ROOT = Join-Path $root 'mai/mai_uploads'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $root 'portable/portable-runtimes/playwright-browsers'
$env:MEMORY_DB_PATH = Join-Path $root 'mai/mcp_tools/mcp_memorizer/MAI_MEMORY/memory.sqlite3'
$env:SENTENCE_BREAKER_DB_PATH = Join-Path $root 'mai/mcp_tools/mcp_memorizer/MAI_MEMORY/sentence_breaker.sqlite3'
$env:CHAT_DB_PATH = Join-Path $root 'mai/mcp_tools/mcp_memorizer/MAI_MEMORY/chat.sqlite3'
$env:LOG_DB_PATH = Join-Path $root 'mai/mcp_tools/mcp_memorizer/MAI_MEMORY/log.sqlite3'
$guardEnabled = [bool]$catalog.memory_guard_enabled
$routerPort = if ($guardEnabled) { 8081 } else { 8080 }
$env:LLAMACPP_MEMORY_GUARD_ENABLED = if ($guardEnabled) { 'true' } else { 'false' }
$env:LLAMACPP_MIN_FREE_VRAM_MIB = [string]$catalog.min_free_vram_mib
$env:MAI_MIN_AVAILABLE_RAM_MIB = [string]$catalog.min_available_ram_mib
$env:LLM_BACKEND = 'llamacpp'
$env:MAIN_MODEL = $defaultModel
$env:LLAMACPP_HOST = 'http://127.0.0.1:8080'
$env:LLAMACPP_MAIN_VISION = if ($mainProjector -or $MainVision) { 'true' } else { 'false' }
# Start-Process ArgumentList needs quoted file paths.
$arguments = @('--models-preset', ('"' + $ini + '"'), '--models-max', "$ModelsMax",
               '--host', '127.0.0.1', '--port', "$routerPort", '--jinja')
if ($guardEnabled) { $arguments += @('--models-memory-margin', [string]$catalog.min_free_vram_mib) }
if ($preserveReasoning) { $arguments += '--reasoning-preserve' } else { $arguments += '--no-reasoning-preserve' }
$process = $null
$gateway = $null
try {
    $process = Start-Process -FilePath $server -ArgumentList $arguments -WorkingDirectory $iniDir -PassThru -NoNewWindow
    $ready = $false
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        if ($process.HasExited) { throw "llama.cpp router exited with $($process.ExitCode)" }
        try {
            $listing = Invoke-RestMethod -Uri "http://127.0.0.1:$routerPort/v1/models" -TimeoutSec 2
            $ids = @($listing.data | ForEach-Object { $_.id })
            if ($ids -contains $defaultModel -and @($catalog.models | Where-Object { $ids -notcontains $_.id }).Count -eq 0) { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw 'Router model aliases not registered within startup deadline' }
    if ($guardEnabled) {
        $gatewayArgs = @('-m', 'portable.llamacpp_memory_gateway',
            '--upstream', 'http://127.0.0.1:8081',
            '--port', '8080',
            '--min-free-vram-mib', [string]$catalog.min_free_vram_mib,
            '--min-available-ram-mib', [string]$catalog.min_available_ram_mib,
            '--telemetry-path', ('"' + (Join-Path $root 'mai/mcp_tools/mcp_memorizer/MAI_MEMORY/gpu_ram_safety.jsonl') + '"'))
        $gateway = Start-Process -FilePath $python -ArgumentList $gatewayArgs -WorkingDirectory $root -PassThru -NoNewWindow
        $gatewayReady = $false
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            if ($gateway.HasExited) { throw "Memory gateway exited with $($gateway.ExitCode)" }
            try {
                $listing = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/v1/models' -TimeoutSec 2
                if (@($listing.data).Count -gt 0) { $gatewayReady = $true; break }
            } catch { }
            Start-Sleep -Seconds 1
        }
        if (-not $gatewayReady) { throw 'Memory gateway did not become ready' }
    }
    Write-Host 'MAI llama.cpp router endpoint ready on 127.0.0.1:8080; '
    Write-Host "Shared memory guard: $guardEnabled"
    Write-Host 'MAI Portable single-resident mode: max one GGUF loaded by llama.cpp router.'
    if ($RouterOnly) {
        Write-Host 'MAI Portable router-only mode: no MAI application server started.'
        Wait-Process -Id $process.Id
        if ($process.ExitCode -ne 0) { throw "llama.cpp router exited with $($process.ExitCode)" }
    } elseif ($GaiaBenchmark) {
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
    if ($null -ne $gateway -and -not $gateway.HasExited) {
        & taskkill.exe /PID $gateway.Id /T /F 2>$null | Out-Null
    }
    if ($null -ne $process -and -not $process.HasExited) {
        # A router can spawn children; terminate the entire process tree.
        & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
    }
}
