#requires -version 5.1
# dev2 is portable-only; delegate to the single-model provisioning script.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Manifest,
    [string]$ModelFile,
    [switch]$InstallChromium,
    [switch]$PreserveAndReinstallExisting
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$installer = Join-Path $root 'portable/prepare-portable.ps1'
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw "Missing portable installer: $installer" }
$args = @('-Manifest', $Manifest)
if ($ModelFile) { $args += @('-ModelFile', $ModelFile) }
if ($InstallChromium) { $args += '-InstallChromium' }
if ($PreserveAndReinstallExisting) { $args += '-PreserveAndReinstallExisting' }
& $installer @args
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "Portable installation failed with code $LASTEXITCODE" }
