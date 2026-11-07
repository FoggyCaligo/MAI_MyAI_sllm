# Prepare a portable MAI tree from trusted, pinned release archives.
# All network downloads must specify exact source URLs and SHA-256 digests.
param(
    [Parameter(Mandatory=$true)][string]$Manifest,
    [string]$ModelFile,
    [switch]$InstallChromium,
    [switch]$PreserveAndReinstallExisting
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$manifestPath = (Resolve-Path -LiteralPath $Manifest).Path
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
# Resolve the destination from settings.yaml before any runtime downloads.
# Use the existing portable Python where available; otherwise the Python
# already used to generate portable-sources.json. PyYAML is required.
$settingsPath = Join-Path $root 'settings.yaml'
$helperPath = Join-Path $PSScriptRoot 'resolve_model_path.py'
$portablePython = Join-Path $PSScriptRoot 'portable-runtimes/python/python.exe'
if (Test-Path -LiteralPath $portablePython -PathType Leaf) {
    $bootstrapPython = $portablePython
} else {
    $bootstrapCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $bootstrapCommand) {
        throw 'Python with PyYAML is required to read MODEL_GGUF_PATH before provisioning. Install Python or provide the existing portable Python runtime.'
    }
    $bootstrapPython = $bootstrapCommand.Source
}
$configuredPath = & $bootstrapPython $helperPath $settingsPath
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($configuredPath)) {
    throw 'Cannot read settings.yaml MODEL_GGUF_PATH before provisioning (Python with PyYAML required).'
}
$configuredPath = $configuredPath.Trim()
$targetFile = if ([IO.Path]::IsPathRooted($configuredPath)) { $configuredPath } else { Join-Path $root $configuredPath }
$targetFile = [IO.Path]::GetFullPath($targetFile)
if ([IO.Path]::GetExtension($targetFile) -ine '.gguf') {
    throw "MODEL_GGUF_PATH must end in .gguf: $targetFile"
}
$selectedModel = if ($ModelFile) { $ModelFile } else { $targetFile }
if (-not (Test-Path -LiteralPath $selectedModel -PathType Leaf)) {
    throw "Model not found: $selectedModel. Put a GGUF at MODEL_GGUF_PATH or provide -ModelFile to copy a GGUF there."
}
if ($ModelFile -and [IO.Path]::GetExtension($selectedModel) -ine '.gguf') {
    throw "-ModelFile must point to a .gguf file: $selectedModel"
}
$preflightStream = [System.IO.File]::OpenRead($selectedModel)
try {
    $preflightMagic = New-Object byte[] 4
    if ($preflightStream.Read($preflightMagic, 0, 4) -ne 4 -or [Text.Encoding]::ASCII.GetString($preflightMagic) -cne 'GGUF') {
        throw "Selected model does not have a GGUF header: $selectedModel"
    }
} finally {
    $preflightStream.Dispose()
}
$sourceFullPath = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $selectedModel).Path)
if ($ModelFile -and $sourceFullPath -ine $targetFile -and (Test-Path -LiteralPath $targetFile -PathType Leaf) -and -not $PreserveAndReinstallExisting) {
    throw "Configured GGUF already exists: $targetFile. Use -PreserveAndReinstallExisting to back up before replacing it."
}

function Get-Required([object]$obj, [string]$property) {
    $value = $obj.PSObject.Properties[$property]
    if ($null -eq $value -or [string]::IsNullOrWhiteSpace([string]$value.Value)) {
        throw "Missing required manifest field: $property"
    }
    return [string]$value.Value
}
function Install-VerifiedArchive([object]$entry, [string]$destination, [string]$expectedExecutable) {
    $url = Get-Required $entry 'url'
    $hash = Get-Required $entry 'sha256'
    if ($url -notmatch '^https://') { throw "Only HTTPS release URLs are allowed: $url" }
    if ($hash -notmatch '^[0-9a-fA-F]{64}$') { throw "Invalid SHA-256 for $destination" }
    $cache = Join-Path $root 'portable/portable-downloads'
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $archiveName = [IO.Path]::GetFileName(([uri]$url).AbsolutePath)
    if ($archiveName -notmatch '\.(zip|tar\.gz)$') { throw "Unsupported archive type: $archiveName" }
    $recordPath = Join-Path $destination '.mai-portable-source.json'
    if (Test-Path -LiteralPath $destination) {
        $binary = Join-Path $destination $expectedExecutable
        if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) {
            throw "Existing runtime lacks expected executable: $binary"
        }
        if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) {
            if (-not $PreserveAndReinstallExisting) {
                throw "Existing runtime has no installation receipt: $destination. Rerun with -PreserveAndReinstallExisting to preserve it and reinstall."
            }
            $backup = "$destination.before-portable-$(Get-Date -Format 'yyyyMMddHHmmss')"
            if (Test-Path -LiteralPath $backup) { throw "Preservation destination already exists: $backup" }
            Move-Item -LiteralPath $destination -Destination $backup -ErrorAction Stop
            Write-Host "Preserved existing runtime in $backup"
        } else {
            $record = Get-Content -LiteralPath $recordPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($record.url -cne $url -or $record.sha256 -ine $hash -or $record.executable -cne $expectedExecutable) {
                throw "Existing runtime does not match pinned manifest: $destination"
            }
            Write-Host "Reusing verified runtime: $destination"
            return
        }
    }
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
            $windowsTar = Join-Path $env:SystemRoot "System32\tar.exe"
            if (-not (Test-Path -LiteralPath $windowsTar -PathType Leaf)) { throw "Windows tar.exe not found: $windowsTar" }
            & $windowsTar -xf $archive -C $staging
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
    @{ url = $url; sha256 = $hash.ToLowerInvariant(); executable = $expectedExecutable } |
        ConvertTo-Json | Set-Content -LiteralPath $recordPath -Encoding UTF8

}

$config = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($config.schema_version -ne 1) { throw 'Unsupported portable provisioning manifest version' }
$runtimes = Join-Path $root 'portable/portable-runtimes'
New-Item -ItemType Directory -Force -Path $runtimes | Out-Null
Install-VerifiedArchive $config.uv (Join-Path $runtimes 'uv') 'uv.exe'
Install-VerifiedArchive $config.python (Join-Path $runtimes 'python') 'python.exe'
if ($null -eq $config.llamacpp) { throw 'Manifest must include pinned llama.cpp Vulkan release.' }
if ($null -ne $config.PSObject.Properties['ollama'] -or $null -ne $config.PSObject.Properties['models']) { throw 'Use a dev2 single-model manifest without Ollama entries.' }
Install-VerifiedArchive $config.llamacpp (Join-Path $runtimes 'llamacpp') 'llama-server.exe'
$python = Join-Path $runtimes 'python\python.exe'
& $python -c 'import sys; print(sys.version)'
if ($LASTEXITCODE -ne 0) { throw 'Portable Python cannot start' }

# Use an independently installed uv command for provisioning only; MAI runtime
# does not depend on uv at startup. Do not silently use global Python for MAI.
$uv = Join-Path $runtimes 'uv\uv.exe'
& $uv pip install --python $python "$root"
if ($LASTEXITCODE -ne 0) { throw 'MAI dependency installation failed' }
& $python -c 'import fastapi, uvicorn, playwright, mai'
if ($LASTEXITCODE -ne 0) { throw 'Portable Python import verification failed' }
if ($InstallChromium) {
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $runtimes 'playwright-browsers'
    & $python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Portable Chromium installation failed' }
}
# Model destination is the same MODEL_GGUF_PATH that start-portable.ps1 uses.
if ($ModelFile -and $sourceFullPath -ine $targetFile) {
    $targetDir = Split-Path -Parent $targetFile
    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
    if (Test-Path -LiteralPath $targetFile -PathType Leaf) {
        $backup = "$targetFile.before-portable-$(Get-Date -Format 'yyyyMMddHHmmss')"
        if (Test-Path -LiteralPath $backup) { throw "Backup already exists: $backup" }
        Move-Item -LiteralPath $targetFile -Destination $backup -ErrorAction Stop
        Write-Host "Preserved old GGUF: $backup"
    }
    Copy-Item -LiteralPath $sourceFullPath -Destination $targetFile -ErrorAction Stop
    if ((Get-FileHash -LiteralPath $sourceFullPath -Algorithm SHA256).Hash -ne
        (Get-FileHash -LiteralPath $targetFile -Algorithm SHA256).Hash) {
        Remove-Item -LiteralPath $targetFile -Force -ErrorAction SilentlyContinue
        throw 'GGUF copy checksum mismatch'
    }
}

Write-Host "Portable installation prepared under $root"
Write-Host "Model selected via settings.yaml: $targetFile"; Write-Host "Run portable/start-portable.ps1 after checking your llama.cpp compatibility."
