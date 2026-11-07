# Single-GGUF portable MAI. llama.cpp engine is replaceable independently of GGUF.
param(
    [string]$GgufPath,
    [string]$MmprojPath
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
$serverArgs = @('-m', $quotedModelPath, '--alias', $quotedModelAlias,
                '--host', '127.0.0.1', '--port', '8080', '--jinja',
                '--parallel', '1', '--slot-save-path', $quotedCachePath)
if (-not $MmprojPath) {
    if ($env:MODEL_MMPROJ_PATH) {
        $MmprojPath = $env:MODEL_MMPROJ_PATH
    } else {
        $MmprojPath = & $python (Join-Path $root 'portable/resolve_model_path.py') (Join-Path $root 'settings.yaml') --key MODEL_MMPROJ_PATH --optional
        if ($LASTEXITCODE -ne 0) { throw 'Cannot read settings.yaml MODEL_MMPROJ_PATH; see error above.' }
        if ($null -ne $MmprojPath) { $MmprojPath = [string]$MmprojPath }
    }
}
if ($MmprojPath) {
    if (-not [IO.Path]::IsPathRooted($MmprojPath)) { $MmprojPath = Join-Path $root $MmprojPath }
    if (-not (Test-Path -LiteralPath $MmprojPath -PathType Leaf)) { throw "Multimodal projector not found: $MmprojPath" }
    $projectorFile = (Resolve-Path -LiteralPath $MmprojPath -ErrorAction Stop).Path
    if (-not [IO.Path]::GetExtension($projectorFile).Equals('.gguf', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Multimodal projector must be a .gguf file: $projectorFile"
    }
    $projectorStream = [System.IO.File]::OpenRead($projectorFile)
    try {
        $projectorMagic = New-Object byte[] 4
        if ($projectorStream.Read($projectorMagic, 0, 4) -ne 4 -or [Text.Encoding]::ASCII.GetString($projectorMagic) -cne 'GGUF') {
            throw "Multimodal projector file is not a GGUF: $projectorFile"
        }
    } finally { $projectorStream.Dispose() }
    $serverArgs += @('--mmproj', ('"' + $projectorFile + '"'))
}
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
