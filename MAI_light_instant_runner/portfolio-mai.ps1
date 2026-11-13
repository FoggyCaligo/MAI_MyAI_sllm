# Portfolio-only Windows runner. No account setup, Tailscale or remote worker.
param([ValidateSet('start','end','restart','check')][string]$Command='start')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = $PSScriptRoot
Set-Location -LiteralPath $root
$python = Join-Path $root 'portable/portable-runtimes/python/python.exe'
$llama = Join-Path $root 'portable/portable-runtimes/llamacpp/llama-server.exe'
$router = Join-Path $root 'portable/start-portable-llamacpp-router.ps1'
function Assert-Ready {
    foreach ($path in @($python,$llama,$router,(Join-Path $root 'settings.yaml'))) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Portfolio runtime missing: $path. Rebuild with -IncludeRuntime -IncludeModels."
        }
    }
    & $python (Join-Path $root 'portable/check_llamacpp_catalog.py') --root $root
    if ($LASTEXITCODE -ne 0) { throw 'No valid GGUF catalog/model found.' }
}
switch ($Command) {
    'check' { Assert-Ready; Write-Host 'MAI portfolio runtime/catalog checks passed.' }
    'end' {
        if (-not (Test-Path -LiteralPath $python)) { throw 'Portable Python missing' }
        & $python (Join-Path $root 'portable/restart_mai.py') --stop
        if ($LASTEXITCODE -ne 0) { throw 'Failed to stop MAI portfolio service' }
    }
    'restart' {
        & $PSCommandPath end
        if ($LASTEXITCODE -ne 0) { throw 'Stop failed; restart cancelled' }
        & $PSCommandPath start
        if ($LASTEXITCODE -ne 0) { throw 'Restart failed' }
    }
    'start' {
        Assert-Ready
        $env:MAI_HOST = '127.0.0.1'
        $env:MAI_PORT = '8000'
        $env:MAI_CWD = $root
        $env:MAI_UPLOAD_ROOT = Join-Path $root 'mai/mai_uploads'
        $env:PYTHONNOUSERSITE = '1'
        & $router
        if ($LASTEXITCODE -ne 0) { throw "MAI portfolio exited with code $LASTEXITCODE" }
    }
}
