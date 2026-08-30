[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$adminRole = [Security.Principal.WindowsBuiltInRole]::Administrator
if (-not $principal.IsInRole($adminRole)) {
    throw 'Run this script from an elevated PowerShell window.'
}

$publicKey = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOkWXV/wSXmpftcz8sjMVckOdyvZX+Tf1R+OJh6NC+U8 u0_a511@localhost'
$sshDirectory = 'C:\ProgramData\ssh'
$authorizedKeysPath = Join-Path $sshDirectory 'administrators_authorized_keys'
$configPath = Join-Path $sshDirectory 'sshd_config'

if (-not (Test-Path -LiteralPath $configPath)) {
    throw "OpenSSH configuration was not found at $configPath"
}

New-Item -ItemType Directory -Path $sshDirectory -Force | Out-Null
$existingKeys = if (Test-Path -LiteralPath $authorizedKeysPath) {
    @(Get-Content -LiteralPath $authorizedKeysPath | Where-Object { $_.Trim() })
} else {
    @()
}
if ($publicKey -notin $existingKeys) {
    $existingKeys += $publicKey
}
Set-Content -LiteralPath $authorizedKeysPath -Value $existingKeys -Encoding ascii

$administratorsSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$systemSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$acl = [Security.AccessControl.FileSecurity]::new()
$acl.SetOwner($administratorsSid)
$acl.SetAccessRuleProtection($true, $false)
$fullControl = [Security.AccessControl.FileSystemRights]::FullControl
$allow = [Security.AccessControl.AccessControlType]::Allow
$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($administratorsSid, $fullControl, $allow))
$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($systemSid, $fullControl, $allow))
Set-Acl -LiteralPath $authorizedKeysPath -AclObject $acl

$config = Get-Content -LiteralPath $configPath
function Set-SshDirective {
    param([string[]]$Lines, [string]$Name, [string]$Value)
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
$config = Set-SshDirective -Lines $config -Name 'PasswordAuthentication' -Value 'no'
$config = Set-SshDirective -Lines $config -Name 'KbdInteractiveAuthentication' -Value 'no'
$config = Set-SshDirective -Lines $config -Name 'PermitEmptyPasswords' -Value 'no'
Set-Content -LiteralPath $configPath -Value $config -Encoding ascii

& "$env:WINDIR\System32\OpenSSH\sshd.exe" -t
Restart-Service -Name sshd

$fingerprint = & "$env:WINDIR\System32\OpenSSH\ssh-keygen.exe" -lf $authorizedKeysPath
$resultPath = Join-Path $PSScriptRoot 'termux_ssh_key_result.txt'
@(
    'STATUS=OK'
    'PUBLIC_KEY_AUTH=enabled'
    'PASSWORD_AUTH=disabled'
    'KEYBOARD_INTERACTIVE_AUTH=disabled'
    "AUTHORIZED_KEYS=$authorizedKeysPath"
    "FINGERPRINT=$fingerprint"
) | Set-Content -LiteralPath $resultPath -Encoding utf8

Get-Content -LiteralPath $resultPath
