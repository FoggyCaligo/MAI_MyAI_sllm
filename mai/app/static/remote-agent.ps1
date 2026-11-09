param(
    [Parameter(Mandatory=$true)][string]$Server,
    [Parameter(Mandatory=$true)][string]$Session,
    [Parameter(Mandatory=$true)][string]$Key
)
$ErrorActionPreference = 'Stop'
if (-not $Server.StartsWith('https://', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'HTTPS MAI server required'
}
$Server = $Server.TrimEnd('/')
Write-Host "MAI temporary remote management on $env:COMPUTERNAME ($env:USERNAME)"
Write-Host 'The connected MAI can read, modify and delete files, and execute commands with your Windows account permissions.'
$headers = @{'X-MAI-Agent-Secret'=$Key}
function Initialize-MaiRemotePython {
    $base = Join-Path $env:LOCALAPPDATA 'MAI\\RemoteWorker'
    $zipPath = Join-Path $env:TEMP ('mai-remote-' + [guid]::NewGuid().ToString() + '.zip')
    $uri = "$Server/remote/agent/$Session/bootstrap"
    try {
        $response = Invoke-WebRequest -Uri $uri -Headers $headers -OutFile $zipPath -PassThru -UseBasicParsing
        $digest = [string]$response.Headers['X-MAI-Bundle-SHA256']
        $actual = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ([string]::IsNullOrWhiteSpace($digest) -or $actual -cne $digest.ToLowerInvariant()) {
            throw 'MAI worker bundle checksum mismatch'
        }
        $target = Join-Path $base $digest
        $sourceReady = Join-Path $target '.installed-ok'
        # Source ZIP is a verified exact digest. The Python environment is
        # separate and reused as long as its declared dependencies are unchanged.
        if (-not (Test-Path -LiteralPath $sourceReady -PathType Leaf)) {
            New-Item -ItemType Directory -Force -Path $target | Out-Null
            Expand-Archive -LiteralPath $zipPath -DestinationPath $target -Force
        }
        if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
            if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
                throw 'Python 3.11+ is missing, and winget is unavailable to install it automatically'
            }
            & winget install --id Python.Python.3.12 --exact --accept-source-agreements --accept-package-agreements --silent
            if ($LASTEXITCODE -ne 0) { throw "Python setup failed (winget exit $LASTEXITCODE)" }
        }
        $pyCommand = Get-Command py -ErrorAction SilentlyContinue
        if (-not $pyCommand) {
            $installed = Join-Path $env:LOCALAPPDATA 'Programs\\Python\\Python312\\python.exe'
            if (-not (Test-Path -LiteralPath $installed -PathType Leaf)) {
                throw 'Python setup completed but the Python executable cannot be resolved'
            }
            $pythonCommand = $installed
            $pythonArgs = @()
        } else {
            $pythonCommand = $pyCommand.Source
            $pythonArgs = @('-3.12')
        }
        $env:PYTHONPATH = $target
        & $pythonCommand @pythonArgs -m mai.remote.bootstrap_env --base $base --source $target
        if ($LASTEXITCODE -ne 0) { throw 'Remote shared dependency installation failed' }
        $envDigestFile = Join-Path $target '.shared-env-key'
        if (-not (Test-Path -LiteralPath $envDigestFile -PathType Leaf)) {
            throw 'Remote dependency environment identity is missing'
        }
        $envDigest = (Get-Content -LiteralPath $envDigestFile -Raw).Trim()
        if ($envDigest -cnotmatch '^[0-9a-f]{64}$') {
            throw 'Remote dependency environment identity is invalid'
        }
        $venvPython = Join-Path (Join-Path (Join-Path $base '.shared-envs') $envDigest) 'Scripts\\python.exe'
        if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
            throw "Remote dependency Python executable is missing: $venvPython"
        }
        $env:MAI_REMOTE_CACHE_ROOT = $base
        $env:MAI_REMOTE_CACHE_DIGEST = $digest
        $env:MAI_REMOTE_ENV_DIGEST = $envDigest
        $env:MAI_REMOTE_PYTHON = $venvPython
        $env:PYTHONPATH = $target
        Write-Host "MAI remote Python worker ready: $target"
    } finally {
        Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
    }
}

Initialize-MaiRemotePython
$env:MAI_REMOTE_SERVER = $Server
$env:MAI_REMOTE_SESSION = $Session
$env:MAI_REMOTE_KEY = $Key
Write-Host 'Connected. Close this PowerShell window or press Ctrl+C to stop accepting work.'
try {
    & $env:MAI_REMOTE_PYTHON -m mai.remote.agent_client
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0 -and $exitCode -ne 130) {
        throw "MAI remote agent exited with code $exitCode"
    }
} finally {
    Remove-Item Env:MAI_REMOTE_CACHE_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:MAI_REMOTE_CACHE_DIGEST -ErrorAction SilentlyContinue
    Remove-Item Env:MAI_REMOTE_ENV_DIGEST -ErrorAction SilentlyContinue
    Remove-Item Env:MAI_REMOTE_SERVER -ErrorAction SilentlyContinue
    Remove-Item Env:MAI_REMOTE_SESSION -ErrorAction SilentlyContinue
    Remove-Item Env:MAI_REMOTE_KEY -ErrorAction SilentlyContinue
}
