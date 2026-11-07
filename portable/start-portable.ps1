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

if (-not $GgufPath) {
    $GgufPath = & $python (Join-Path $root "portable/resolve_model_path.py") (Join-Path $root "settings.yaml")
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($GgufPath)) { throw "Cannot read settings.yaml MODEL_GGUF_PATH; see error above." }
    $GgufPath = $GgufPath.Trim()
}
if (-not [IO.Path]::IsPathRooted($GgufPath)) { $GgufPath = Join-Path $root $GgufPath }
if (-not (Test-Path -LiteralPath $GgufPath -PathType Leaf)) {
    throw "GGUF model not found: $GgufPath. Copy a real GGUF file to portable/portable-models/gguf/active.gguf, or pass -GgufPath with its actual location. The previous D:\\models\\my-model.gguf was an example only."
}
$modelFile = (Resolve-Path -LiteralPath $GgufPath -ErrorAction Stop).Path
if (-not [IO.Path]::GetExtension($modelFile).Equals('.gguf', [StringComparison]::OrdinalIgnoreCase)) {
    throw "Single portable model must be a .gguf file: $modelFile"
}
$ggufStream = [System.IO.File]::OpenRead($modelFile)
try {
    $magic = New-Object byte[] 4
    if ($ggufStream.Read($magic, 0, 4) -ne 4 -or [Text.Encoding]::ASCII.GetString($magic) -cne 'GGUF') {
        throw "Model file is not a GGUF: $modelFile"
    }
} finally {
    $ggufStream.Dispose()
}
$visionConfigured = & $python -c 'import sys,yaml; cfg=yaml.safe_load(open(sys.argv[1],encoding="utf-8-sig")) or {}; print(str(cfg.get("VISION_GGUF_PATH") or "")); print(str(cfg.get("VISION_MMPROJ_PATH") or ""))' (Join-Path $root 'settings.yaml')
if ($LASTEXITCODE -ne 0) { throw 'Cannot read vision configuration from settings.yaml' }
$visionPath = [string]$visionConfigured[0]
$projectorPath = [string]$visionConfigured[1]
$visionMode = -not [string]::IsNullOrWhiteSpace($visionPath)
if ($projectorPath -and -not $visionMode) { throw 'VISION_MMPROJ_PATH requires VISION_GGUF_PATH' }
if ($visionMode) {
    if (-not [IO.Path]::IsPathRooted($visionPath)) { $visionPath = Join-Path $root $visionPath }
    if (-not (Test-Path -LiteralPath $visionPath -PathType Leaf)) { throw "Vision GGUF does not exist: $visionPath" }
    $visionPath = (Resolve-Path -LiteralPath $visionPath).Path
    if (-not $visionPath.EndsWith('.gguf', [StringComparison]::OrdinalIgnoreCase)) { throw 'Vision model must be .gguf' }
    if ($projectorPath) {
        if (-not [IO.Path]::IsPathRooted($projectorPath)) { $projectorPath = Join-Path $root $projectorPath }
        if (-not (Test-Path -LiteralPath $projectorPath -PathType Leaf)) { throw "Vision projector does not exist: $projectorPath" }
        $projectorPath = (Resolve-Path -LiteralPath $projectorPath).Path
    }
}
$modelAlias = "mai-model"

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
$env:MAI_ROUTER_MODE = if ($visionMode) { 'true' } else { 'false' }
$env:MAI_VISION_MODEL = if ($visionMode) { 'mai-vision' } else { '' }
# Exact launched GGUF identity for model-scoped cache filenames (honors -GgufPath).
$env:MAI_ACTIVE_GGUF_PATH = $modelFile
$env:LLM_BACKEND = 'llamacpp'
$env:LLAMACPP_HOST = 'http://127.0.0.1:8080'
$env:MAI_SLOT_CACHE_DIR = Join-Path $root 'portable/portable-slot-cache'
New-Item -ItemType Directory -Force -Path $env:MAI_SLOT_CACHE_DIR | Out-Null
# Ignore obsolete multi-model environment overrides from earlier MAI builds.
Remove-Item Env:VERIFIER_MODEL,Env:VISION_MODEL,Env:OLLAMA_HOST,Env:OLLAMA_MODELS -ErrorAction SilentlyContinue

if (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 8080 is occupied. Stop the old llama.cpp server before launching portable MAI.'
}
# Start-Process joins ArgumentList items into a Windows command line.
# Explicitly quote filesystem paths so portable SSD paths with spaces survive.
$quotedModelPath = '"' + $modelFile + '"'
$quotedCachePath = '"' + $env:MAI_SLOT_CACHE_DIR + '"'
$quotedModelAlias = '"' + $modelAlias + '"'
$presetPath = Join-Path $env:MAI_SLOT_CACHE_DIR 'router-models.ini'
if ($visionMode) {
    # Router enforces at most one model resident. Model KV is rebuilt from
    # conversation after a swap; slot checkpoint restore is disabled.
    $normalize = { param([string]$path) ($path -replace '\\', '/') }
    $mainIni = & $normalize $modelFile
    $visionIni = & $normalize $visionPath
    $ini = @("[mai-model]", "model = $mainIni", "parallel = 1", "",
              "[mai-vision]", "model = $visionIni", "parallel = 1")
    if ($projectorPath) { $ini += "mmproj = $(& $normalize $projectorPath)" }
    $ini | Set-Content -LiteralPath $presetPath -Encoding UTF8
    $serverArgs = @('--models-preset', ('"' + $presetPath + '"'),
                    '--models-max', '1', '--host', '127.0.0.1',
                    '--port', '8080', '--jinja')
} else {
    $serverArgs = @('-m', $quotedModelPath, '--alias', $quotedModelAlias,
                    '--host', '127.0.0.1', '--port', '8080', '--jinja',
                    '--parallel', '1', '--slot-save-path', $quotedCachePath)
}
$llamaProcess = Start-Process -FilePath $llama -ArgumentList $serverArgs -WorkingDirectory (Split-Path $llama) -PassThru -NoNewWindow
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 100; $attempt++) {
        if ($llamaProcess.HasExited) { throw "llama.cpp exited with code $($llamaProcess.ExitCode)" }
        try {
            $response = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/v1/models' -TimeoutSec 2
            if ($visionMode -or ($response.data.Count -eq 1 -and $response.data[0].id -eq $modelAlias)) {
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
    if ($visionMode) { Remove-Item -LiteralPath $presetPath -Force -ErrorAction SilentlyContinue }
}
