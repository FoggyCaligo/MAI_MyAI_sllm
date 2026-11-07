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
# Reject a missing example model immediately, before downloading runtimes/dependencies.
if ($ModelFile -and -not (Test-Path -LiteralPath $ModelFile -PathType Leaf)) {
    throw "-ModelFile not found: $ModelFile. Provide an existing .gguf path; D:\\models\\my-model.gguf is documentation placeholder, not a bundled model."
}


# Validate the selected model before provisioning any runtime.
# A runtime install without a usable model can otherwise appear successful.
$defaultModel = Join-Path $root 'portable/portable-models/gguf/active.gguf'
$selectedModel = if ($ModelFile) { $ModelFile } else { $defaultModel }
if (-not (Test-Path -LiteralPath $selectedModel -PathType Leaf)) {
    throw "No GGUF model available before provisioning: $selectedModel. Supply -ModelFile with an existing GGUF or place active.gguf in portable/portable-models/gguf/."
}
if ([IO.Path]::GetExtension($selectedModel) -ine '.gguf') {
    throw "Selected model must have .gguf extension: $selectedModel"
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
# Model weights remain independent from the llama.cpp runtime directory.
if ($ModelFile) {
    $sourceFile = (Resolve-Path -LiteralPath $ModelFile -ErrorAction Stop).Path
    if ([IO.Path]::GetExtension($sourceFile) -ine '.gguf') { throw '-ModelFile must point to a GGUF file.' }
    $ggufStream = [System.IO.File]::OpenRead($sourceFile)
    try {
        $magic = New-Object byte[] 4
        if ($ggufStream.Read($magic, 0, 4) -ne 4 -or [Text.Encoding]::ASCII.GetString($magic) -cne 'GGUF') {
            throw "-ModelFile is not GGUF content: $sourceFile"
        }
    } finally {
        $ggufStream.Dispose()
    }
    $targetDir = Join-Path $root 'portable/portable-models/gguf'
    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
    $targetFile = Join-Path $targetDir 'active.gguf'
    if (([IO.Path]::GetFullPath($sourceFile)) -ne ([IO.Path]::GetFullPath($targetFile))) {
        if (Test-Path -LiteralPath $targetFile -PathType Leaf) {
            if (-not $PreserveAndReinstallExisting) {
                throw 'active.gguf already exists. Pass -PreserveAndReinstallExisting to back it up before replacing.'
            }
            $backup = "$targetFile.before-portable-$(Get-Date -Format 'yyyyMMddHHmmss')"
            Move-Item -LiteralPath $targetFile -Destination $backup -ErrorAction Stop
            Write-Host "Preserved old GGUF: $backup"
        }
        Copy-Item -LiteralPath $sourceFile -Destination $targetFile -ErrorAction Stop
        if ((Get-FileHash -LiteralPath $sourceFile -Algorithm SHA256).Hash -ne
            (Get-FileHash -LiteralPath $targetFile -Algorithm SHA256).Hash) {
            Remove-Item -LiteralPath $targetFile -Force -ErrorAction SilentlyContinue
            throw 'GGUF copy checksum mismatch'
        }
    }
}
Write-Host "Portable installation prepared under $root"
Write-Host 'Verify portable/portable-models/gguf/active.gguf and run portable/start-portable.ps1. If no model was copied, supply a real GGUF file or use -GgufPath.'
