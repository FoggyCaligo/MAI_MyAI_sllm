#requires -version 5.1
[CmdletBinding()]
param(
    [string]$InstallPath = (Join-Path $env:USERPROFILE 'Documents\Git\MAI_MyAI_sllm')
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Run-Command {
    param([string]$Executable, [Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable exited with code $LASTEXITCODE"
    }
}

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
        [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + $env:Path
}

function Ensure-Command {
    param([string]$Name, [string]$WingetId)
    if (Get-Command $Name -ErrorAction SilentlyContinue) { return }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget is unavailable. Install App Installer from Microsoft Store and retry."
    }
    Write-Host "Installing $WingetId..."
    Run-Command 'winget' @('install', '--id', $WingetId, '--exact', '--accept-source-agreements', '--accept-package-agreements')
    Refresh-Path
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$WingetId was installed but $Name is not yet available. Open a new PowerShell and rerun this script."
    }
}


function Get-MsvcInstallation {
    $vswhere = Join-Path ([Environment]::GetFolderPath('ProgramFilesX86')) 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path -LiteralPath $vswhere)) { return $null }
    $installation = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($LASTEXITCODE -ne 0) { throw "vswhere failed with exit code $LASTEXITCODE" }
    if (-not $installation) { return $null }
    return ($installation | Select-Object -First 1)
}

function Ensure-MsvcBuildTools {
    if (Get-MsvcInstallation) {
        Write-Host 'Microsoft C++ compiler toolset already installed.'
        return
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'winget is required to install Microsoft C++ Build Tools.'
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Microsoft C++ Build Tools installation requires an administrator PowerShell.'
    }
    Write-Host 'Installing Microsoft Visual Studio Build Tools with Desktop C++ workload...'
    Run-Command 'winget' @(
        'install', '--id', 'Microsoft.VisualStudio.BuildTools', '--exact',
        '--accept-source-agreements', '--accept-package-agreements',
        '--override', '--passive --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended'
    )
    if (-not (Get-MsvcInstallation)) {
        throw 'Microsoft C++ Build Tools did not register the VC compiler. Verify the Visual Studio Installer workload and rerun.'
    }
}

function Read-RequiredValue {
    param([string]$Prompt)
    $value = Read-Host $Prompt
    if ([string]::IsNullOrWhiteSpace($value)) { throw "$Prompt cannot be empty" }
    return $value
}

if ($env:OS -ne 'Windows_NT') {
    throw 'This installer supports Windows only.'
}
Write-Host 'MAI first-run setup - existing .env and databases will be preserved.'
Ensure-Command 'git' 'Git.Git'
Ensure-Command 'python' 'Python.Python.3.12'
Ensure-Command 'ollama' 'Ollama.Ollama'
Ensure-MsvcBuildTools

$parent = Split-Path -Parent $InstallPath
New-Item -ItemType Directory -Force -Path $parent | Out-Null
if (-not (Test-Path -LiteralPath $InstallPath)) {
    Run-Command 'git' @('clone', '--branch', 'dev', 'https://github.com/FoggyCaligo/MAI_MyAI_sllm.git', $InstallPath)
}
elseif (-not (Test-Path -LiteralPath (Join-Path $InstallPath '.git'))) {
    throw "Install path exists but is not a Git repository: $InstallPath"
}

Push-Location $InstallPath
try {
    $venvPython = Join-Path $InstallPath '.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        Run-Command 'python' @('-m', 'venv', '.venv')
    }
    Run-Command $venvPython @('-m', 'pip', 'install', '--upgrade', 'pip')
    Run-Command $venvPython @('-m', 'pip', 'install', '-e', '.')
    Run-Command $venvPython @('-m', 'playwright', 'install', 'chromium')
    Run-Command $venvPython @('-c', 'import resemblyzer, faster_whisper, numpy; print("Voice dependencies OK")')

    $envPath = Join-Path $InstallPath '.env'
    if (-not (Test-Path -LiteralPath $envPath)) {
        $model = Read-RequiredValue 'Ollama model name to download (e.g. gemma3:4b)'
        Run-Command 'ollama' @('pull', $model)
        $userId = Read-RequiredValue 'Owner login ID'
        $secure = Read-Host 'Owner password' -AsSecureString
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
            if ([string]::IsNullOrEmpty($password)) { throw 'Owner password cannot be empty' }
            $ownerJson = ConvertTo-Json -Compress -InputObject @(@{
                user_id = $userId
                user_pw = $password
                db_id = 'local-user'
            })
        }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }
        $source = Get-Content '.env.example' -Raw -Encoding UTF8
        $lines = @(
            $source -split "\r?\n" | ForEach-Object {
                if ($_ -match '^MAIN_MODEL=') { 'MAIN_MODEL=' + $model }
                elseif ($_ -match '^OWNER_USERS=') { 'OWNER_USERS=' + $ownerJson }
                elseif ($_ -match '^TRIAL_USERS=') { 'TRIAL_USERS=[]' }
                elseif ($_ -match '^TAILSCALE_FUNNEL=') { 'TAILSCALE_FUNNEL=false' }
                elseif ($_ -match '^VISION_MODEL=') { 'VISION_MODEL=' }
                else { $_ }
            }
        )
        [System.IO.File]::WriteAllText($envPath, ($lines -join [Environment]::NewLine), (New-Object System.Text.UTF8Encoding($false)))
        Write-Host 'Created .env: local-only, Trial disabled, Funnel disabled.'
    }
    else {
        Write-Host 'Existing .env preserved; no login, model, or Funnel settings changed.'
    }

    Write-Host ''
    Write-Host "Installation complete: $InstallPath"
    Write-Host "To start (in a non-administrator terminal):"
    Write-Host "  cd '$InstallPath'"
    Write-Host '  .\.venv\Scripts\python.exe run_server.py'
    Write-Host 'Tailscale/Funnel is intentionally NOT enabled by this installer.'
}
finally {
    Pop-Location
}
