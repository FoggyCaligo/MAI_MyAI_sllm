[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$diagnosticPath = Join-Path $PSScriptRoot 'tailscale_ssh_error.txt'
Remove-Item -LiteralPath $diagnosticPath -ErrorAction SilentlyContinue
trap {
    @(
        "ERROR_TYPE=$($_.Exception.GetType().FullName)"
        "ERROR_MESSAGE=$($_.Exception.Message)"
        "ERROR_POSITION=$($_.InvocationInfo.PositionMessage)"
        "ERROR_STACK=$($_.ScriptStackTrace)"
    ) | Set-Content -LiteralPath $diagnosticPath -Encoding utf8
    exit 1
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$adminRole = [Security.Principal.WindowsBuiltInRole]::Administrator
if (-not $principal.IsInRole($adminRole)) {
    throw 'Run this script from an elevated PowerShell window.'
}

$capability = Get-WindowsCapability -Online |
    Where-Object Name -Like 'OpenSSH.Server*' |
    Select-Object -First 1
if (-not $capability) {
    throw 'OpenSSH.Server Windows capability was not found.'
}
if ($capability.State -ne 'Installed') {
    Add-WindowsCapability -Online -Name $capability.Name | Out-Null
}

$sshDirectory = 'C:\ProgramData\ssh'
$configPath = Join-Path $sshDirectory 'sshd_config'
if (-not (Test-Path -LiteralPath $configPath)) {
    New-Item -ItemType Directory -Path $sshDirectory -Force | Out-Null
    $defaultConfigPath = "$env:WINDIR\System32\OpenSSH\sshd_config_default"
    if (-not (Test-Path -LiteralPath $defaultConfigPath)) {
        throw "OpenSSH default configuration was not found at $defaultConfigPath"
    }
    Copy-Item -LiteralPath $defaultConfigPath -Destination $configPath
}
if (-not (Test-Path -LiteralPath $configPath)) {
    throw "OpenSSH configuration was not created at $configPath"
}

$backupPath = "$configPath.mai-backup"
if (-not (Test-Path -LiteralPath $backupPath)) {
    Copy-Item -LiteralPath $configPath -Destination $backupPath
}

$config = Get-Content -LiteralPath $configPath
function Set-SshDirective {
    param(
        [string[]]$Lines,
        [string]$Name,
        [string]$Value
    )
    $replacement = "$Name $Value"
    $matched = $false
    $output = foreach ($line in $Lines) {
        if (-not $matched -and $line -match "^\s*#?\s*$([regex]::Escape($Name))\s+") {
            $matched = $true
            $replacement
        } else {
            $line
        }
    }
    if (-not $matched) {
        $output = @($replacement) + @($output)
    }
    return @($output)
}

$config = Set-SshDirective -Lines $config -Name 'PubkeyAuthentication' -Value 'yes'
$config = Set-SshDirective -Lines $config -Name 'PasswordAuthentication' -Value 'yes'
$config = Set-SshDirective -Lines $config -Name 'PermitEmptyPasswords' -Value 'no'
$config = Set-SshDirective -Lines $config -Name 'PermitRootLogin' -Value 'no'
Set-Content -LiteralPath $configPath -Value $config -Encoding ascii

& "$env:WINDIR\System32\OpenSSH\sshd.exe" -t

Set-Service -Name sshd -StartupType Automatic

$defaultRule = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
if ($defaultRule) {
    Disable-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' | Out-Null
}

$ruleName = 'MAI-Tailscale-SSH-In-TCP'
Remove-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
New-NetFirewallRule `
    -Name $ruleName `
    -DisplayName 'MAI SSH over Tailscale only' `
    -Enabled True `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort 22 `
    -RemoteAddress '100.64.0.0/10' `
    -Profile Any | Out-Null

Restart-Service -Name sshd

$tailscalePath = 'C:\Program Files\Tailscale\tailscale.exe'
$tailscaleIp = if (Test-Path -LiteralPath $tailscalePath) {
    (& $tailscalePath ip -4 | Select-Object -First 1).Trim()
} else {
    '(Tailscale executable not found)'
}

$resultPath = Join-Path $PSScriptRoot 'tailscale_ssh_result.txt'
@(
    'STATUS=OK'
    "WINDOWS_USER=$env:USERNAME"
    "TAILSCALE_IP=$tailscaleIp"
    'SSH_PORT=22'
    'PASSWORD_AUTH=temporarily-enabled'
    "CONFIG_BACKUP=$backupPath"
) | Set-Content -LiteralPath $resultPath -Encoding utf8

Write-Host ''
Write-Host 'MAI Tailscale SSH setup completed.' -ForegroundColor Green
Get-Content -LiteralPath $resultPath
