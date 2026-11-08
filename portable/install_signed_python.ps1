# Install an official, signed CPython runtime inside MAI's Windows portable folder.
# No machine-wide Python registration or automatic runtime updates.
param(
    [Parameter(Mandatory=$true)][string]$Root,
    [ValidateSet('setup','update')][string]$Mode = 'setup',
    [ValidatePattern('^3\.11\.[0-9]+$')][string]$Version = '3.11.9',
    [switch]$PreserveAndReinstallExisting
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$runtime = Join-Path $Root 'portable/portable-runtimes'
$destination = Join-Path $runtime 'python'
$uv = Join-Path $runtime 'uv/uv.exe'
$metadataName = '.mai-signed-python.json'

function Assert-SignedPython([string]$folder, [string]$expectedVersion) {
    $exe = Join-Path $folder 'python.exe'
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "Python missing: $exe" }
    $signature = Get-AuthenticodeSignature -LiteralPath $exe
    if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate -or
        $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
        throw "Python must have a valid Python Software Foundation Authenticode signature: $exe ($($signature.Status))"
    }
    $actual = & $exe --version
    if ($LASTEXITCODE -ne 0 -or @($actual).Count -ne 1 -or $actual -ne "Python $expectedVersion") {
        throw "Expected Python $expectedVersion; got $actual"
    }
}

function Test-MaiServerRunning {
    foreach ($port in @(8000,8080)) {
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $task = $client.ConnectAsync('127.0.0.1', $port)
            if ($task.Wait(300) -and $client.Connected) {
                throw "Port $port is active; stop MAI and llama.cpp before changing the Python runtime"
            }
        } catch [AggregateException] {
            # Connection refused: not listening. Other failures are not a reason to replace a running runtime.
        } finally { $client.Dispose() }
    }
}

function Install-MaiDependencies([string]$folder) {
    if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) { throw "Portable uv missing: $uv" }
    $exe = Join-Path $folder 'python.exe'
    $env:UV_LINK_MODE = 'copy'
    $env:PYTHONNOUSERSITE = '1'
    & $uv pip install --python $exe --reinstall-package mai-myai-sllm "$Root[dev]"
    if ($LASTEXITCODE -ne 0) { throw 'Installing MAI dependencies failed; existing runtime not replaced' }
    & $exe -c 'import fastapi, uvicorn, playwright, mai, pytest'
    if ($LASTEXITCODE -ne 0) { throw 'MAI dependency import preflight failed; existing runtime not replaced' }
}

if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) { throw 'Run ./mai.sh setup to install portable uv first' }
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$existing = Test-Path -LiteralPath $destination -PathType Container
$existingValid = $false
if ($existing) {
    try {
        Assert-SignedPython $destination $Version
        $existingValid = $true
    } catch {
        Write-Warning "Existing Python is not the selected signed runtime: $_"
    }
}
if ($Mode -eq 'setup' -and $existingValid) {
    Write-Host "Reusing signed portable Python $Version in $destination"
    Install-MaiDependencies $destination
    # An adopted copy is explicitly marked as such: no fabricated download provenance.
    $marker = Join-Path $destination $metadataName
    if (-not (Test-Path -LiteralPath $marker)) {
        @{ provider='python.org'; install_method='adopted-signed-runtime'; version=$Version; executable='python.exe' } |
            ConvertTo-Json | Set-Content -LiteralPath $marker -Encoding UTF8
    }
    return
}
if ($Mode -eq 'update' -and $existingValid) {
    Write-Host "Python $Version is already installed. No runtime update needed."
    return
}
if ($Mode -eq 'setup' -and $existing -and -not $PreserveAndReinstallExisting) {
    throw 'Existing Python is not the selected signed runtime. Rerun setup with -PreserveAndReinstallExisting to back it up before replacement.'
}
Test-MaiServerRunning

$manager = Get-Command pymanager -ErrorAction SilentlyContinue
if ($null -eq $manager) { $manager = Get-Command py -ErrorAction SilentlyContinue }
if ($null -eq $manager) {
    throw 'Python install manager is required for explicit setup/update. Install it from python.org; no global Python runtime is needed after provisioning.'
}
$staging = Join-Path $runtime 'python.incoming'
if (Test-Path -LiteralPath $staging) {
    throw "Previous incomplete Python installation exists: $staging. Inspect it before retrying."
}
$committed = $false
$backup = $null
try {
    # The Python install manager verifies the official python.org index signature.
    # A full patch version is selected explicitly; never use a floating 3.11 tag.
    & $manager.Source install "--target=$staging" $Version
    if ($LASTEXITCODE -ne 0) { throw "Python install manager failed for version $Version" }
    Assert-SignedPython $staging $Version
    Install-MaiDependencies $staging
    @{ provider='python.org'; install_method='python-install-manager-target'; version=$Version; executable='python.exe' } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $staging $metadataName) -Encoding UTF8

    if ($existing) {
        $backup = Join-Path $runtime ("python.backup-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        if (Test-Path -LiteralPath $backup) { throw "Backup path already exists: $backup" }
        Move-Item -LiteralPath $destination -Destination $backup -ErrorAction Stop
    }
    try {
        Move-Item -LiteralPath $staging -Destination $destination -ErrorAction Stop
        $committed = $true
    } catch {
        if ($null -ne $backup -and (Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $destination)) {
            Move-Item -LiteralPath $backup -Destination $destination
        }
        throw
    }
    Write-Host "Signed portable Python $Version installed: $destination"
    if ($null -ne $backup) { Write-Host "Previous Python retained for manual rollback: $backup" }
} finally {
    if (-not $committed -and (Test-Path -LiteralPath $staging)) {
        Write-Warning "Uncommitted Python stage retained for diagnosis: $staging"
    }
}
