#requires -Version 5.1
<# Local, minimal MAI portfolio distribution (no account/remote/hosting code).
   The production MAI source tree is never edited by this builder. #>
[CmdletBinding()]
param([switch]$IncludeRuntime, [switch]$IncludeModels)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$output = Join-Path $PSScriptRoot 'MAI'
$project = Join-Path $PSScriptRoot 'launcher/MAI.Launcher.csproj'
$publish = Join-Path $PSScriptRoot '.launcher-build'
if ($PSVersionTable.PSEdition -eq 'Core' -and -not $IsWindows) { throw 'Windows required' }
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { throw 'Install .NET 8 SDK first' }
if (Test-Path -LiteralPath $output -PathType Container) {
    $other = @(Get-ChildItem -LiteralPath $output -Force | Where-Object { $_.Name -ne '.gitkeep' })
    if ($other.Count) { throw "Existing distribution will not be overwritten: $output. Back it up/remove it first." }
}

& dotnet publish $project -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -p:DebugType=None -p:DebugSymbols=false -p:PublishTrimmed=false -o $publish
if ($LASTEXITCODE -ne 0) { throw 'MAI.exe .NET compilation failed' }
$exe = Join-Path $publish 'MAI.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'MAI.exe missing after build' }

# Only the modules reachable by this separate portfolio entrypoint.
# Never copy the production mai.app Python backend, mai.remote, mai.hosting,
# authentication/session modules, production tool implementations or agents.
$core = @(
    'mai/__init__.py',
    'mai/agent/__init__.py', 'mai/agent/guards.py', 'mai/agent/loop.py',
    'mai/agent/runtime.py', 'mai/agent/tool_results.py', 'mai/agent/verification.py',
    'mai/agent/verifier_prompts/action_grounding.md',
    'mai/agent/verifier_prompts/factual.md',
    'mai/agent/verifier_prompts/numeric.md',
    'mai/agent/verifier_prompts/quality_review.md',
    'mai/llm/__init__.py', 'mai/llm/models.py', 'mai/llm/errors.py',
    'mai/llm/llamacpp.py', 'mai/llm/llamacpp_router.py',
    'mai/llm/reasoning_monitor.py', 'mai/llm/stream_diagnostics.py',
    'mai/tools/artifacts.py', 'mai/tools/registry.py',
    'mai/tools/calculator.py', 'mai/tools/time.py',
    'mai/mcp_tools/__init__.py', 'mai/mcp_tools/tool_manual.py',
    'portable/check_llamacpp_catalog.py',
    'portable/llamacpp_model_catalog.py',
    'portable/resolve_llamacpp_settings.py',
    'portable/llamacpp_memory_gateway.py',
    'portable/restart_mai.py', 'portable/runtime_data_paths.py',
    'portable/start-portable-llamacpp-router.ps1',
    'settings.yaml', 'pyproject.toml'
)
foreach ($path in $core) {
    $source = Join-Path $repo $path
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Required MAI core file missing: $source" }
}

New-Item -ItemType Directory -Force -Path $output | Out-Null
Copy-Item -LiteralPath $exe -Destination (Join-Path $output 'MAI.exe') -Force
foreach ($path in $core) {
    $dest = Join-Path $output $path
    New-Item -ItemType Directory -Force -Path (Split-Path $dest -Parent) | Out-Null
    Copy-Item -LiteralPath (Join-Path $repo $path) -Destination $dest -Force
}

# The original mai.tools.__init__ imports production-only file/web/remote
# services. Replace it *only inside the distribution*, not in the source tree.
[IO.File]::WriteAllText(
    (Join-Path $output 'mai/tools/__init__.py'),
    '"""Light portfolio tools: calculator, time and memory only."""' + [Environment]::NewLine,
    (New-Object Text.UTF8Encoding($false))
)
New-Item -ItemType Directory -Force -Path (Join-Path $output 'scripts') | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'portfolio_server.py') -Destination (Join-Path $output 'scripts/portfolio_server.py')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'portfolio-mai.ps1') -Destination (Join-Path $output 'mai.ps1')

# Switch the Portable launcher to the minimal backend, only in the copy.
$routerScript = Join-Path $output 'portable/start-portable-llamacpp-router.ps1'
$routerContent = [IO.File]::ReadAllText($routerScript)
if (-not $routerContent.Contains('& $python scripts/run_server.py')) { throw 'Portable launcher entrypoint changed: update portfolio compatibility' }
$routerContent = $routerContent.Replace('& $python scripts/run_server.py', '& $python scripts/portfolio_server.py')
$routerContent = $routerContent.Replace("if (-not (Test-Path -LiteralPath (Join-Path $root '.env') -PathType Leaf)) { throw 'Missing .env' }", '')
[IO.File]::WriteAllText($routerScript, $routerContent, (New-Object Text.UTF8Encoding($false)))

# Preserve the EXISTING MAI chat presentation and JS, rather than inventing
# a second UI. Remove only the login/remote startup flow in this generated copy.
$originalUI = Join-Path $repo 'mai/app/static/index.html'
$ui = [IO.File]::ReadAllText($originalUI)
if (-not $ui.Contains('  restoreSession();')) { throw 'MAI UI startup contract changed; refusing to package unverified UI' }
$ui = $ui.Replace('  restoreSession();', "  state.token='portfolio-local'; state.userId='portfolio-local'; state.role='trial'; showApp();")
$ui = $ui.Replace('recoverRemoteSession().catch(err=>{$remoteError.textContent=err.message;});', '')
$ui = $ui.Replace('</head>', '<style id="mai-portfolio-local">#login-screen,#remote-btn,#remote-panel,#logout-btn,#upload-btn,#clipboard-previews,.verify-switch{display:none!important}#app{display:flex!important}</style></head>')
$web = Join-Path $output 'mai/app/static'
New-Item -ItemType Directory -Force -Path $web | Out-Null
[IO.File]::WriteAllText((Join-Path $web 'index.html'), $ui, (New-Object Text.UTF8Encoding($false)))
Copy-Item -LiteralPath (Join-Path $repo 'mai/app/static/voice-input.js') -Destination (Join-Path $web 'voice-input.js')

# The standalone backend listens strictly on 127.0.0.1 and never loads
# the production account/remote API, Tailscale, Cloudflare or credentials.
foreach ($relative in @('mai/mai_uploads', 'mai/mcp_tools/mcp_memorizer/MAI_MEMORY',
                         'portable/portable-runtimes','portable/portable-models')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $output $relative) | Out-Null
}

foreach ($license in @('LICENSE','LICENSE.txt','LICENSE.md')) {
    if (Test-Path -LiteralPath (Join-Path $repo $license) -PathType Leaf) {
        Copy-Item -LiteralPath (Join-Path $repo $license) -Destination (Join-Path $output $license)
    }
}

if ($IncludeRuntime) {
    $source = Join-Path $repo 'portable/portable-runtimes'
    foreach ($required in @('python/python.exe','llamacpp/llama-server.exe')) {
        if (-not (Test-Path -LiteralPath (Join-Path $source $required) -PathType Leaf)) {
            throw "IncludeRuntime requested but missing: $required"
        }
    }
    Copy-Item -Path (Join-Path $source '*') -Destination (Join-Path $output 'portable/portable-runtimes') -Recurse -Force
    $python = Join-Path $output 'portable/portable-runtimes/python/python.exe'
    # Re-link the installed package, which might still reference the original checkout.
    & $python -m pip install --disable-pip-version-check --no-build-isolation --no-deps -e $output
    if ($LASTEXITCODE -ne 0) { throw 'Portable Python package relinking failed. Do not distribute.' }
    & $python -c 'import mai.agent.runtime, mai.tools.calculator, mcp_memorizer; print("portfolio imports OK")'
    if ($LASTEXITCODE -ne 0) { throw 'Trimmed MAI package import check failed. Do not distribute.' }
}
if ($IncludeModels) {
    $models = Join-Path $repo 'portable/portable-models'
    if (-not (Test-Path -LiteralPath $models -PathType Container)) {
        throw 'No model files available to include'
    }
    Write-Warning 'Check individual GGUF model redistribution licenses before sharing.'
    Copy-Item -Path (Join-Path $models '*') -Destination (Join-Path $output 'portable/portable-models') -Recurse -Force
}
Write-Host "Portfolio distribution: $output"
Write-Host "Original MAI chat UI retained; account/Tailscale/remote production code omitted."
if (-not $IncludeRuntime -or -not $IncludeModels) {
    Write-Warning 'This build needs Portable runtime and GGUF models before it can answer.'
}
Write-Warning 'No personal DBs, private MCP config, credentials, tokens or uploads were included.'
