#requires -Version 5.1
<#
Build a separate Windows MAI.exe distribution under ./MAI_light_instant_runner/MAI.
Git-tracked application source only; runtime and models are opt-in.
Requires Windows, Git and .NET 8 SDK.
#>
[CmdletBinding()]
param([switch]$IncludeRuntime, [switch]$IncludeModels)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$output = Join-Path $PSScriptRoot 'MAI'
$project = Join-Path $PSScriptRoot 'launcher/MAI.Launcher.csproj'
$publish = Join-Path $PSScriptRoot '.launcher-build'

if ($PSVersionTable.PSEdition -eq 'Core' -and -not $IsWindows) {
    throw 'Build this Windows EXE on Windows.'
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git is required to assemble tracked source.'
}
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw 'Install the .NET 8 SDK to build MAI.exe.'
}
if (Test-Path -LiteralPath $output -PathType Container) {
    $existing = @(Get-ChildItem -LiteralPath $output -Force | Where-Object { $_.Name -ne '.gitkeep' })
    if ($existing.Count -gt 0) {
        throw "Destination is not empty: $output. Move the previous build; it will not be deleted."
    }
}

Write-Host 'Publishing self-contained Windows MAI.exe...'
& dotnet publish $project -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -p:DebugType=None -p:DebugSymbols=false -p:PublishTrimmed=false -o $publish
if ($LASTEXITCODE -ne 0) { throw 'dotnet publish failed; distribution output was not overwritten.' }
$exe = Join-Path $publish 'MAI.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'MAI.exe was not produced.' }

New-Item -ItemType Directory -Force -Path $output | Out-Null
Copy-Item -LiteralPath $exe -Destination (Join-Path $output 'MAI.exe')

# Never copy untracked credentials or data; include only source under known paths.
$allowedPrefixes = @('mai/', 'portable/', 'scripts/')
$allowedFiles = @('mai.ps1', 'mai.sh', 'mai.cmd', 'run_server.py', 'settings.yaml', '.env.example', 'pyproject.toml', 'README.md', 'LICENSE', 'LICENSE.txt', 'LICENSE.md')
$tracked = @(& git -C $repo -c core.quotepath=false ls-files)
if ($LASTEXITCODE -ne 0 -or $tracked.Count -eq 0) { throw 'Unable to list tracked source files.' }
foreach ($relative in $tracked) {
    $relative = $relative.Trim()
    if (-not $relative) { continue }
    $normalized = $relative.Replace('\', '/')
    $allowed = $allowedFiles -contains $normalized
    foreach ($prefix in $allowedPrefixes) {
        if ($normalized.StartsWith($prefix, [StringComparison]::Ordinal)) { $allowed = $true }
    }
    if (-not $allowed) { continue }
    if ($normalized -match '(^|/)(MAI_MEMORY|mai_uploads|__pycache__|portable-runtimes|portable-models|portable-downloads)/') { continue }
    if ($normalized -match '(^|/)(mcp_servers\.json|mcp_catalog\.json|\.env)$') { continue }
    if ($normalized -match '\.(sqlite3|sqlite3-wal|sqlite3-shm|db|log|jsonl)$') { continue }
    $src = Join-Path $repo $normalized
    if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { continue }
    $dest = Join-Path $output $normalized
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    Copy-Item -LiteralPath $src -Destination $dest
}
foreach ($relative in @('mai/mai_uploads', 'mai/mcp_tools/mcp_memorizer/MAI_MEMORY', 'portable/portable-runtimes', 'portable/portable-models')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $output $relative) | Out-Null
}

if ($IncludeRuntime) {
    $runtimeSource = Join-Path $repo 'portable/portable-runtimes'
    if (-not (Test-Path -LiteralPath (Join-Path $runtimeSource 'python/python.exe') -PathType Leaf)) {
        throw 'Portable Python missing. Run MAI setup on this PC first.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $runtimeSource 'llamacpp/llama-server.exe') -PathType Leaf)) {
        throw 'Portable llama.cpp missing. Run MAI setup first.'
    }
    Write-Host 'Copying locally installed Portable runtimes...'
    Copy-Item -Path (Join-Path $runtimeSource '*') -Destination (Join-Path $output 'portable/portable-runtimes') -Recurse -Force
    $python = Join-Path $output 'portable/portable-runtimes/python/python.exe'
    Write-Host 'Re-linking editable package for distribution directory...'
    & $python -m pip install --disable-pip-version-check --no-build-isolation --no-deps -e $output
    if ($LASTEXITCODE -ne 0) { throw 'Editable dependency relinking failed. Do not distribute this folder.' }
}
if ($IncludeModels) {
    $models = Join-Path $repo 'portable/portable-models'
    if (-not (Test-Path -LiteralPath $models -PathType Container)) { throw 'No installed GGUF model directory.' }
    Write-Warning 'Confirm license and redistribution rights before sharing GGUF models.'
    Copy-Item -Path (Join-Path $models '*') -Destination (Join-Path $output 'portable/portable-models') -Recurse -Force
}

Write-Host "MAI Windows distribution: $output"
Write-Host "GUI launcher: $(Join-Path $output 'MAI.exe')"
if (-not $IncludeRuntime) { Write-Warning 'Portable runtimes excluded. Run the Setup action on the destination PC.' }
if (-not $IncludeModels) { Write-Warning 'GGUF models excluded. Install/download models separately.' }
Write-Warning 'Private .env, uploads, memory DBs and MCP configurations were not copied.'
