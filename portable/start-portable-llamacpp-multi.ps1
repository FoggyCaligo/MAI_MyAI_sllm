# Optional llama.cpp Portable for dev. Existing Ollama runner stays unchanged.
# Separate child servers are intentionally simultaneously resident (no model swap).
param(
    [Parameter(Mandatory=$true)][string]$MainGgufPath,
    [Parameter(Mandatory=$true)][string]$VerifierGgufPath,
    [string]$MainMmprojPath,
    [string]$VisionGgufPath,
    [string]$VisionMmprojPath,
    [ValidateRange(0,999)][int]$GpuLayers = 99,
    [ValidateRange(512,262144)][int]$ContextSize = 16384
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root 'portable/portable-runtimes/python/python.exe'
$server = Join-Path $root 'portable/portable-runtimes/llamacpp/llama-server.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Missing Portable Python: $python" }
if (-not (Test-Path -LiteralPath $server -PathType Leaf)) { throw "Missing llama-server.exe: $server" }
if (-not (Test-Path -LiteralPath (Join-Path $root '.env') -PathType Leaf)) {
    throw 'Missing .env; create from .env.example first.'
}
if ($VisionMmprojPath -and -not $VisionGgufPath) { throw '-VisionMmprojPath requires -VisionGgufPath' }

function Resolve-Gguf([string]$raw, [string]$label) {
    if (-not $raw) { return $null }
    $path = if ([IO.Path]::IsPathRooted($raw)) { $raw } else { Join-Path $root $raw }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "$label file not found: $path" }
    $resolved = (Resolve-Path -LiteralPath $path).Path
    if (-not $resolved.EndsWith('.gguf', [StringComparison]::OrdinalIgnoreCase)) {
        throw "$label must have .gguf extension: $resolved"
    }
    $stream = [IO.File]::OpenRead($resolved)
    try {
        $bytes = New-Object byte[] 4
        if ($stream.Read($bytes, 0, 4) -ne 4 -or [Text.Encoding]::ASCII.GetString($bytes) -cne 'GGUF') {
            throw "$label has invalid GGUF magic: $resolved"
        }
    } finally { $stream.Dispose() }
    return $resolved
}

$main = Resolve-Gguf $MainGgufPath 'Main model'
$verifier = Resolve-Gguf $VerifierGgufPath 'Verifier model'
$mainProjector = Resolve-Gguf $MainMmprojPath 'Main multimodal projector'
$vision = Resolve-Gguf $VisionGgufPath 'Vision model'
$visionProjector = Resolve-Gguf $VisionMmprojPath 'Vision multimodal projector'
if ($vision -and -not $visionProjector) {
    Write-Warning 'Separate vision GGUF has no mmproj; verify whether its architecture can encode images without one.'
}
# Make endpoint ownership explicit: do not silently connect to existing servers.
$ports = @(8080, 8081)
if ($vision) { $ports += 8082 }
foreach ($port in $ports) {
    if (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is occupied; stop the other process before launching MAI."
    }
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
$env:LLAMACPP_VERIFIER_HOST = 'http://127.0.0.1:8081'
$env:LLAMACPP_VISION_HOST = if ($vision) { 'http://127.0.0.1:8082' } else { 'http://127.0.0.1:8080' }
$env:LLAMACPP_MAIN_VISION = if ($mainProjector) { 'true' } else { 'false' }
$env:VISION_MODEL = if ($vision) { 'mai-vision' } elseif ($mainProjector) { 'mai-main' } else { '' }

# Start-Process joins ArgumentList; explicit quotes preserve paths with spaces.
function Quote-Argument([string]$arg) { return '"' + $arg + '"' }
function Start-ModelServer([string]$file, [string]$alias, [int]$port, [string]$projector) {
    $args = @('-m', (Quote-Argument $file), '--alias', (Quote-Argument $alias),
              '--host', '127.0.0.1', '--port', "$port", '--jinja',
              '--parallel', '1', '--ctx-size', "$ContextSize", '--n-gpu-layers', "$GpuLayers")
    if ($projector) { $args += @('--mmproj', (Quote-Argument $projector)) }
    $p = Start-Process -FilePath $server -ArgumentList $args -WorkingDirectory (Split-Path $server) -PassThru -NoNewWindow
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        if ($p.HasExited) { throw "llama-server $alias exited with $($p.ExitCode)" }
        try {
            $data = Invoke-RestMethod -Uri "http://127.0.0.1:$port/v1/models" -TimeoutSec 2
            if (@($data.data | Where-Object { $_.id -eq $alias }).Count -eq 1) { return $p }
        } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
    throw "llama-server $alias not ready on port $port"
}
$processes = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
try {
    # Fail closed if either required GGUF cannot start; never fall back to Ollama.
    $processes.Add((Start-ModelServer $main 'mai-main' 8080 $mainProjector))
    $processes.Add((Start-ModelServer $verifier 'mai-verifier' 8081 $null))
    if ($vision) { $processes.Add((Start-ModelServer $vision 'mai-vision' 8082 $visionProjector)) }
    Write-Host 'MAI dev optional llama.cpp mode: independent servers running; GGUFs may coexist in VRAM/RAM.'
    & $python run_server.py
    if ($LASTEXITCODE -ne 0) { throw "MAI server exited with $LASTEXITCODE" }
} finally {
    foreach ($p in $processes) {
        if (-not $p.HasExited) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
