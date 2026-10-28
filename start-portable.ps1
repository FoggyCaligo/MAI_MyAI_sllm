param(
    [ValidateSet('ollama','llamacpp')]
    [string]$Backend = 'ollama'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$python = Join-Path $root 'portable-runtimes\python\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Portable Python not found: $python. Provision the portable runtime following docs/portable-windows.md."
}
$envPath = Join-Path $root '.env'
if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
    throw "Missing local .env; create from .env.example and configure credentials."
}
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:OLLAMA_MODELS = Join-Path $root 'portable-models\ollama'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $root 'portable-runtimes\playwright-browsers'
$env:MAI_CWD = $root
$env:MAI_UPLOAD_ROOT = Join-Path $root 'mai_uploads'
$env:MEMORY_DB_PATH = Join-Path $root 'data\memory.sqlite3'
$env:SENTENCE_BREAKER_DB_PATH = Join-Path $root 'data\sentence_breaker.sqlite3'
$env:CHAT_DB_PATH = Join-Path $root 'data\chat.sqlite3'
$env:LOG_DB_PATH = Join-Path $root 'data\log.sqlite3'
$env:LLM_BACKEND = $Backend
$ollama = Join-Path $root 'portable-runtimes\ollama\ollama.exe'
$llama = Join-Path $root 'portable-runtimes\llamacpp\llama-server.exe'
if ($Backend -eq 'ollama') {
    if (-not (Test-Path -LiteralPath $ollama -PathType Leaf)) { throw "Portable Ollama runtime is missing: $ollama" }
    if (-not (Test-Path -LiteralPath $env:OLLAMA_MODELS -PathType Container)) {
        throw "Portable Ollama model directory is missing: $env:OLLAMA_MODELS"
    }
    $ollamaProcess = Start-Process -FilePath $ollama -ArgumentList 'serve' -WorkingDirectory (Split-Path $ollama) -PassThru -NoNewWindow
    try {
        $ready = $false
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            if ($ollamaProcess.HasExited) { throw "Ollama exited with code $($ollamaProcess.ExitCode)" }
            try {
                $response = Invoke-WebRequest -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 -UseBasicParsing
                if ($response.StatusCode -eq 200) { $ready = $true; break }
            } catch [System.Net.WebException] { }
            Start-Sleep -Milliseconds 500
        }
        if (-not $ready) { throw 'Ollama startup did not become ready in 20 seconds' }
        & $python run_server.py
        if ($LASTEXITCODE -ne 0) { throw "MAI exited with code $LASTEXITCODE" }
    } finally {
        if (-not $ollamaProcess.HasExited) { Stop-Process -Id $ollamaProcess.Id }
    }
} else {
    if (-not (Test-Path -LiteralPath $llama -PathType Leaf)) { throw "Portable llama.cpp runtime is missing: $llama" }
    throw 'llama.cpp portable launch needs an explicit GGUF path and alias; see docs/portable-windows.md. Start llama-server manually, then launch MAI with LLM_BACKEND=llamacpp. No implicit model selection.'
}
