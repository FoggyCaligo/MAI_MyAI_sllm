# One entry point for the Windows Portable runtime. No alternate providers or silent fallbacks.
param(
    [Parameter(Position=0)]
    [ValidateSet('start', 'setup', 'check')]
    [string]$Command = 'start',
    [switch]$PreserveAndReinstallExisting
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = $PSScriptRoot
Set-Location -LiteralPath $root
$portable = Join-Path $root 'portable'
$runtime = Join-Path $portable 'portable-runtimes'
$manifest = Join-Path $portable 'portable-sources.json'
$python = Join-Path $runtime 'python/python.exe'
$llama = Join-Path $runtime 'llamacpp/llama-server.exe'
$envFile = Join-Path $root '.env'
$settings = Join-Path $root 'settings.yaml'
$launcher = Join-Path $portable 'start-portable-llamacpp-router.ps1'

function Require-File([string]$path, [string]$description) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "$description missing: $path"
    }
}

function Assert-PortableReady {
    Require-File $settings 'MAI settings.yaml'
    Require-File $envFile 'MAI .env'
    Require-File $python 'Portable Python'
    Require-File $llama 'Portable llama.cpp'
    Require-File $launcher 'llama.cpp Portable launcher'

    # Validate the same GGUF model catalog as the real launcher, without writing
    # any model preset or switching the currently loaded resident model.
    $env:PYTHONNOUSERSITE = '1'
    & $python (Join-Path $portable 'check_llamacpp_catalog.py') --root $root
    if ($LASTEXITCODE -ne 0) {
        throw 'Portable GGUF catalog validation failed'
    }
}

function Initialize-MaiOwnerEnv {
    if (Test-Path -LiteralPath $envFile -PathType Leaf) {
        Write-Host 'Existing .env preserved.'
        return
    }
    $template = Join-Path $root '.env.example'
    Require-File $template 'MAI .env template'
    $ownerId = Read-Host 'Owner login ID'
    if ([string]::IsNullOrWhiteSpace($ownerId)) { throw 'Owner login ID cannot be empty' }
    $secret = Read-Host 'Owner password' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    try {
        $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        if ([string]::IsNullOrWhiteSpace($password)) { throw 'Owner password cannot be empty' }
        $owner = ConvertTo-Json -Compress -InputObject @(@{ user_id=$ownerId; user_pw=$password; db_id='local-user' })
        $source = Get-Content -LiteralPath $template -Raw -Encoding UTF8
        $lines = @($source -split "\r?\n" | ForEach-Object {
            if ($_ -match '^OWNER_USERS=') { 'OWNER_USERS=' + $owner }
            elseif ($_ -match '^TRIAL_USERS=') { 'TRIAL_USERS=[]' }
            else { $_ }
        })
        [IO.File]::WriteAllText($envFile, ($lines -join [Environment]::NewLine), (New-Object Text.UTF8Encoding($false)))
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

function Initialize-MaiPinnedManifest {
    if (Test-Path -LiteralPath $manifest -PathType Leaf) { return }
    $resolver = Join-Path $portable 'resolve_portable_sources.py'
    Require-File $resolver 'Pinned release resolver'
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) {
        $pyCommand = Get-Command py -ErrorAction SilentlyContinue
        if ($null -ne $pyCommand) {
            & $pyCommand.Source -3 $resolver
            if ($LASTEXITCODE -ne 0) { throw 'Portable manifest resolver failed' }
            return
        }
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($null -eq $winget) {
            throw 'First setup needs Python to generate pinned runtime manifest; neither Python nor winget is available'
        }
        & $winget.Source install --id Python.Python.3.12 --exact --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0) { throw 'System Python bootstrap failed' }
        $candidate = Join-Path $env:LOCALAPPDATA 'Programs/Python/Python312/python.exe'
        Require-File $candidate 'Installed Python 3.12'
        & $candidate $resolver
    } else {
        & $pythonCommand.Source $resolver
    }
    if ($LASTEXITCODE -ne 0) { throw 'Portable manifest resolver failed' }
}

function Invoke-MaiSetup {
    Initialize-MaiPinnedManifest
    Require-File $manifest 'Pinned Portable source manifest'
    $manifestPath = (Resolve-Path -LiteralPath $manifest).Path
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
if ($null -eq $config.llamacpp) { throw 'Pinned manifest must provide llama.cpp' }
Install-VerifiedArchive $config.llamacpp (Join-Path $runtimes 'llamacpp') 'llama-server.exe'
$python = Join-Path $runtimes 'python\python.exe'
& $python -c 'import sys; print(sys.version)'
if ($LASTEXITCODE -ne 0) { throw 'Portable Python cannot start' }

# Use an independently installed uv command for provisioning only; MAI runtime
# does not depend on uv at startup. Do not silently use global Python for MAI.
$uv = Join-Path $runtimes 'uv\uv.exe'
& $uv pip install --python $python "$root[dev]"
if ($LASTEXITCODE -ne 0) { throw 'MAI dependency installation failed' }
& $python -c 'import fastapi, uvicorn, playwright, mai, pytest'
if ($LASTEXITCODE -ne 0) { throw 'Portable Python import verification failed' }

    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $runtimes 'playwright-browsers'
    & $python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Portable Chromium installation failed' }
    Initialize-MaiOwnerEnv
    Write-Host "MAI llama.cpp Portable setup complete: $root"
}

switch ($Command) {
    'check' {
        Assert-PortableReady
        Write-Host 'MAI Portable checks passed (files and GGUF catalog; GPU/server startup not tested).'
    }
    'setup' {
        Invoke-MaiSetup
    }
    'start' {
        Assert-PortableReady
        & $launcher
    }
}
