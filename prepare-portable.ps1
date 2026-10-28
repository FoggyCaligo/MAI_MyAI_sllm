# Prepare a portable MAI tree from trusted, pinned release archives.
# All network downloads must specify exact source URLs and SHA-256 digests.
param(
    [Parameter(Mandatory=$true)][string]$Manifest,
    [switch]$CopyLocalModels,
    [switch]$InstallChromium,
    [switch]$PullModels
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Get-Required([object]$obj, [string]$property) {
    $value = $obj.PSObject.Properties[$property]
    if ($null -eq $value -or [string]::IsNullOrWhiteSpace([string]$value.Value)) {
        throw "Missing required manifest field: $property"
    }
    return [string]$value.Value
}
function Install-VerifiedArchive([object]$entry, [string]$destination, [string]$expectedExecutable) {
    if (Test-Path -LiteralPath $destination) {
        throw "Refusing to overwrite existing runtime: $destination"
    }
    $url = Get-Required $entry 'url'
    $hash = Get-Required $entry 'sha256'
    if ($url -notmatch '^https://') { throw "Only HTTPS release URLs are allowed: $url" }
    if ($hash -notmatch '^[0-9a-fA-F]{64}$') { throw "Invalid SHA-256 for $destination" }
    $cache = Join-Path $root 'portable-downloads'
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $archiveName = [IO.Path]::GetFileName(([uri]$url).AbsolutePath)
    if ($archiveName -notmatch '\.(zip|tar\.gz)$') { throw "Unsupported archive type: $archiveName" }
    $archive = Join-Path $cache $archiveName
    if (-not (Test-Path -LiteralPath $archive)) {
        $temporary = "$archive.incomplete"
        try {
            Invoke-WebRequest -Uri $url -OutFile $temporary -ErrorAction Stop
            Move-Item -LiteralPath $temporary -Destination $archive -ErrorAction Stop
        } finally {
            if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
        }
    }
    $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
    if ($actual -ine $hash) { throw "SHA-256 mismatch for $archive. Expected $hash; found $actual" }
    $staging = "$destination.incomplete"
    if (Test-Path -LiteralPath $staging) { throw "Incomplete previous installation exists: $staging" }
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    try {
        if ($archiveName.EndsWith('.zip')) {
            Expand-Archive -LiteralPath $archive -DestinationPath $staging -ErrorAction Stop
        } else {
            & tar -xf $archive -C $staging
            if ($LASTEXITCODE -ne 0) { throw "Archive extraction failed: $archive" }
        }
        $candidate = Get-ChildItem -LiteralPath $staging -Recurse -File -Filter $expectedExecutable |
            Select-Object -First 1
        if ($null -eq $candidate) { throw "Expected runtime executable missing: $expectedExecutable" }
        $sourceDir = $candidate.Directory.FullName
        if ($sourceDir -ne $staging) {
            $normalized = "$destination.normalized"
            New-Item -ItemType Directory -Force -Path $normalized | Out-Null
            Copy-Item -Path (Join-Path $sourceDir '*') -Destination $normalized -Recurse -Force
            Remove-Item -LiteralPath $staging -Recurse -Force
            Move-Item -LiteralPath $normalized -Destination $destination
        } else {
            Move-Item -LiteralPath $staging -Destination $destination
        }
    } catch {
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
        throw
    }
    if (-not (Test-Path -LiteralPath (Join-Path $destination $expectedExecutable))) {
        throw "Runtime executable not found at destination: $destination"
    }
}

$manifestPath = (Resolve-Path -LiteralPath $Manifest).Path
$config = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($config.schema_version -ne 1) { throw 'Unsupported portable provisioning manifest version' }
$runtimes = Join-Path $root 'portable-runtimes'
New-Item -ItemType Directory -Force -Path $runtimes | Out-Null
Install-VerifiedArchive $config.uv (Join-Path $runtimes 'uv') 'uv.exe'
Install-VerifiedArchive $config.python (Join-Path $runtimes 'python') 'python.exe'
Install-VerifiedArchive $config.ollama (Join-Path $runtimes 'ollama') 'ollama.exe'
if ($null -ne $config.llamacpp) {
    Install-VerifiedArchive $config.llamacpp (Join-Path $runtimes 'llamacpp') 'llama-server.exe'
}
$python = Join-Path $runtimes 'python\python.exe'
& $python -c 'import sys; print(sys.version)'
if ($LASTEXITCODE -ne 0) { throw 'Portable Python cannot start' }

# Use an independently installed uv command for provisioning only; MAI runtime
# does not depend on uv at startup. Do not silently use global Python for MAI.
$uv = Join-Path $runtimes 'uv\uv.exe'
& $uv pip install --python $python "$root"
if ($LASTEXITCODE -ne 0) { throw 'MAI dependency installation failed' }
& $python -c 'import fastapi, ollama, uvicorn, playwright, mai'
if ($LASTEXITCODE -ne 0) { throw 'Portable Python import verification failed' }
if ($InstallChromium) {
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $runtimes 'playwright-browsers'
    & $python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Portable Chromium installation failed' }
}
if ($CopyLocalModels) {
    $sourceModels = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { Join-Path $env:USERPROFILE '.ollama\models' }
    if (-not (Test-Path -LiteralPath $sourceModels -PathType Container)) {
        throw "Source Ollama models not found: $sourceModels"
    }
    $targetModels = Join-Path $root 'portable-models\ollama'
    if (Test-Path -LiteralPath $targetModels) { throw "Refusing to overwrite existing models: $targetModels" }
    New-Item -ItemType Directory -Force -Path $targetModels | Out-Null
    & robocopy $sourceModels $targetModels /E /COPY:DAT /DCOPY:DAT /R:1 /W:1
    if ($LASTEXITCODE -ge 8) { throw "Model copy failed with robocopy code $LASTEXITCODE" }
}
if ($PullModels) {
    if ($null -eq $config.models -or @($config.models).Count -eq 0) {
        throw 'Manifest must list exact Ollama model tags when -PullModels is specified'
    }
    if (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue) {
        throw 'Port 11434 is already in use; stop the existing Ollama service before model provisioning'
    }
    $env:OLLAMA_MODELS = Join-Path $root 'portable-models\ollama'
    New-Item -ItemType Directory -Path $env:OLLAMA_MODELS -Force | Out-Null
    $ollamaExe = Join-Path $runtimes 'ollama\ollama.exe'
    $process = Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WorkingDirectory (Split-Path $ollamaExe) -PassThru -NoNewWindow
    try {
        $ready = $false
        for ($attempt = 0; $attempt -lt 50; $attempt++) {
            if ($process.HasExited) { throw 'Ollama exited before becoming ready' }
            try {
                $probe = Invoke-WebRequest -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 -UseBasicParsing
                if ($probe.StatusCode -eq 200) { $ready = $true; break }
            } catch [System.Net.WebException] { }
            Start-Sleep -Milliseconds 500
        }
        if (-not $ready) { throw 'Ollama service did not start' }
        foreach ($model in $config.models) {
            if (-not ($model -is [string]) -or [string]::IsNullOrWhiteSpace($model)) {
                throw 'Model tags must be non-empty strings'
            }
            & $ollamaExe pull $model
            if ($LASTEXITCODE -ne 0) { throw "Model pull failed: $model" }
            & $ollamaExe show $model
            if ($LASTEXITCODE -ne 0) { throw "Model verification failed: $model" }
        }
    } finally {
        if (-not $process.HasExited) { Stop-Process -Id $process.Id }
    }
}
Write-Host "Portable installation prepared under $root"
Write-Host 'Verify all model tags and run start-portable.ps1; this script does not fabricate missing models.'
