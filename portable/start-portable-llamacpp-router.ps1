# Optional single-router llama.cpp Portable launcher for MAI dev.
# All roles use one API endpoint. Ollama launch scripts and MAI prompts are unchanged.
param(
    [ValidateRange(1,3)][int]$ModelsMax = 2,
    [ValidateRange(0,999)][int]$GpuLayers = 99,
    [ValidateRange(512,262144)][int]$ContextSize = 8192
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
# Paths and main vision mode are configured exclusively in settings.yaml.
$resolvedJson = & $python (Join-Path $root 'portable/resolve_llamacpp_settings.py')
if ($LASTEXITCODE -ne 0) { throw 'Could not load llama.cpp GGUF paths from settings.yaml' }
$config = $resolvedJson | ConvertFrom-Json
$MainGgufPath = [string]$config.LLAMACPP_MAIN_GGUF_PATH
$VerifierGgufPath = [string]$config.LLAMACPP_VERIFIER_GGUF_PATH
$MainMmprojPath = [string]$config.LLAMACPP_MAIN_MMPROJ_PATH
$MainVision = [bool]$config.LLAMACPP_MAIN_VISION
$VisionGgufPath = [string]$config.LLAMACPP_VISION_GGUF_PATH
$VisionMmprojPath = [string]$config.LLAMACPP_VISION_MMPROJ_PATH
if ($VisionMmprojPath -and -not $VisionGgufPath) { throw 'LLAMACPP_VISION_MMPROJ_PATH requires LLAMACPP_VISION_GGUF_PATH' }
if ($VisionGgufPath -and $ModelsMax -lt 3) {
    Write-Warning 'Three model aliases configured, but ModelsMax allows only two resident models.'
}
function Resolve-Gguf([string]$raw, [string]$label) {
    if (-not $raw) { return $null }
    $candidate = if ([IO.Path]::IsPathRooted($raw)) { $raw } else { Join-Path $root $raw }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw "$label missing: $candidate" }
    $resolved = (Resolve-Path -LiteralPath $candidate).Path
    $stream = [IO.File]::OpenRead($resolved)
    try {
        $bytes = New-Object byte[] 4
        if ($stream.Read($bytes, 0, 4) -ne 4 -or [Text.Encoding]::ASCII.GetString($bytes) -cne 'GGUF') {
            throw "$label is not a GGUF: $resolved"
        }
    } finally { $stream.Dispose() }
    return $resolved
}
$main = Resolve-Gguf $MainGgufPath 'Main'
$verifier = Resolve-Gguf $VerifierGgufPath 'Verifier'
$mainProjector = Resolve-Gguf $MainMmprojPath 'Main mmproj'
$vision = Resolve-Gguf $VisionGgufPath 'Vision'
$visionProjector = Resolve-Gguf $VisionMmprojPath 'Vision mmproj'
if ($main -eq $verifier) {
    Write-Warning 'The Main and Verifier aliases refer to the same file: reviewer independence is not guaranteed.'
}
# The llama.cpp router INI uses keys identical to CLI flags, without '--'.
# The generated INI is stored outside Git; paths with spaces are supported as plain INI values.
$iniDir = Join-Path $root 'portable/portable-runtimes/llamacpp'
$ini = Join-Path $iniDir 'mai-models.generated.ini'
$iniLines = [System.Collections.Generic.List[string]]::new()
$iniLines.Add('version = 1')
$iniLines.Add('[*]')
$iniLines.Add("ctx-size = $ContextSize")
$iniLines.Add("n-gpu-layers = $GpuLayers")
$iniLines.Add('parallel = 1')
$iniLines.Add('[mai-main]')
$iniLines.Add("model = $main")
if ($mainProjector) { $iniLines.Add("mmproj = $mainProjector") }
$iniLines.Add('[mai-verifier]')
$iniLines.Add("model = $verifier")
if ($vision) {
    $iniLines.Add('[mai-vision]')
    $iniLines.Add("model = $vision")
    if ($visionProjector) { $iniLines.Add("mmproj = $visionProjector") }
}
[IO.File]::WriteAllLines($ini, [string[]]$iniLines, [Text.UTF8Encoding]::new($false))
if (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 8080 occupied; refusing to connect to another router.'
}
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:MAI_CWD = $root
$env:MAI_UPLOAD_ROOT = Join-Path $root 'mai_uploads'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $root 'portable/portable-runtimes/playwright-browsers'
$env:MEMORY_DB_PATH = Join-Path $root 'data/memory.sqlite3'
$env:SENTENCE_BREAKER_DB_PATH = Join-Path $root 'data/sentence_breaker.sqlite3'
$env:CHAT_DB_PATH = Join-Path $root 'data/chat.sqlite3'
$env:LOG_DB_PATH = Join-Path $root 'data/log.sqlite3'
$env:LLM_BACKEND = 'llamacpp'
$env:MAIN_MODEL = 'mai-main'
$env:VERIFIER_MODEL = 'mai-verifier'
$env:LLAMACPP_HOST = 'http://127.0.0.1:8080'
$env:LLAMACPP_MAIN_VISION = if ($mainProjector -or $MainVision) { 'true' } else { 'false' }
$env:VISION_MODEL = if ($vision) { 'mai-vision' } elseif ($mainProjector -or $MainVision) { 'mai-main' } else { '' }
# Start-Process ArgumentList needs quoted file paths.
$arguments = @('--models-preset', ('"' + $ini + '"'), '--models-max', "$ModelsMax",
               '--host', '127.0.0.1', '--port', '8080', '--jinja')
$process = $null
try {
    $process = Start-Process -FilePath $server -ArgumentList $arguments -WorkingDirectory $iniDir -PassThru -NoNewWindow
    $ready = $false
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        if ($process.HasExited) { throw "llama.cpp router exited with $($process.ExitCode)" }
        try {
            $listing = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/v1/models' -TimeoutSec 2
            $ids = @($listing.data | ForEach-Object { $_.id })
            if ($ids -contains 'mai-main' -and $ids -contains 'mai-verifier' -and
                (-not $vision -or $ids -contains 'mai-vision')) { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw 'Router model aliases not registered within startup deadline' }
    Write-Host 'MAI dev llama.cpp router ready on 127.0.0.1:8080; Ollama remains unchanged.'
    Write-Warning 'The two models may coexist in VRAM/RAM, but --models-max does not guarantee simultaneous residency.'
    & $python run_server.py
    if ($LASTEXITCODE -ne 0) { throw "MAI exited with code $LASTEXITCODE" }
} finally {
    if ($null -ne $process -and -not $process.HasExited) {
        # A router can spawn children; terminate the entire process tree.
        & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
    }
}
