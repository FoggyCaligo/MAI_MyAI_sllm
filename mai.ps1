# One entry point for the Windows Portable runtime. No alternate providers or silent fallbacks.
param(
    [Parameter(Position=0)]
    [ValidateSet('start', 'setup', 'check')]
    [string]$Command = 'start'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = $PSScriptRoot
$portable = Join-Path $root 'portable'
$runtime = Join-Path $portable 'portable-runtimes'
$manifest = Join-Path $portable 'portable-sources.json'
$python = Join-Path $runtime 'python/python.exe'
$llama = Join-Path $runtime 'llamacpp/llama-server.exe'
$envFile = Join-Path $root '.env'
$settings = Join-Path $root 'settings.yaml'
$launcher = Join-Path $portable 'start-portable-llamacpp-router.ps1'
$installer = Join-Path $portable 'prepare-portable.ps1'

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
    & $python -c 'from pathlib import Path; from portable.llamacpp_model_catalog import from_settings; import sys; catalog = from_settings(Path(sys.argv[1])); print("GGUF models:", len(catalog["models"])); print("Default GGUF:", next(model["label"] for model in catalog["models"] if model["id"] == catalog["default"]))' $root
    if ($LASTEXITCODE -ne 0) {
        throw 'Portable GGUF catalog validation failed'
    }
}

switch ($Command) {
    'check' {
        Assert-PortableReady
        Write-Host 'MAI Portable checks passed (files and GGUF catalog; GPU/server startup not tested).'
    }
    'setup' {
        Require-File $manifest 'Pinned Portable source manifest (generate with: python portable/resolve_portable_sources.py --include-llamacpp)'
        Require-File $installer 'Portable provisioning script'
        & $installer -Manifest $manifest
        if ($LASTEXITCODE -ne 0) { throw "Portable setup failed (exit code $LASTEXITCODE)" }
    }
    'start' {
        Assert-PortableReady
        & $launcher
        if ($LASTEXITCODE -ne 0) { throw "MAI Portable launcher exited with code $LASTEXITCODE" }
    }
}
