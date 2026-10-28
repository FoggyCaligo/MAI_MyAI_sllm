param(
    [Parameter(Mandatory=$true)][string]$Server,
    [Parameter(Mandatory=$true)][string]$Session
)
$ErrorActionPreference = 'Stop'
if (-not $Server.StartsWith('https://', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'HTTPS MAI server required'
}
$Server = $Server.TrimEnd('/')
Write-Host "MAI temporary remote management on $env:COMPUTERNAME ($env:USERNAME)"
Write-Host 'The connected MAI can read, modify and delete files, and execute commands with your Windows account permissions.'
if ((Read-Host 'Type YES to authorize remote management') -cne 'YES') {
    throw 'Remote control was not authorized'
}
$random = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($random) } finally { $rng.Dispose() }
$key = [BitConverter]::ToString($random).Replace('-', '').ToLowerInvariant()
$pairUrl = "$Server/remote/agent/$Session/pair"
$body = @{agent_key=$key} | ConvertTo-Json -Compress
$pair = Invoke-RestMethod -Method Post -Uri $pairUrl -ContentType 'application/json' -Body $body
Write-Host "Connection code: $($pair.code)"
Write-Host 'Enter the code in the MAI browser connection dialog and approve.'
$headers = @{'X-MAI-Agent-Secret'=$key}
$approved = $false
for ($i = 0; $i -lt 120; $i++) {
    $status = Invoke-RestMethod -Method Post -Uri "$pairUrl/status" -ContentType 'application/json' -Body $body
    if ($status.approved) { $approved = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $approved) { throw 'Pairing was not approved within 4 minutes' }
Write-Host 'Connected. Close this PowerShell window or press Ctrl+C to stop accepting work.'
try {
    while ($true) {
        $reply = Invoke-RestMethod -Uri "$Server/remote/agent/$Session/poll" -Method Post -Headers $headers -TimeoutSec 30
        if ($null -eq $reply.request) { continue }
        $req = $reply.request
        $out = @{request_id=$req.request_id;ok=$false}
        try {
            $a = $req.arguments
            $result = $null
            switch ($req.operation) {
                'file.read' {
                    $bytes = [IO.File]::ReadAllBytes($a.path)
                    if ($bytes.Length -gt 2000000) { throw 'File exceeds 2 MB response limit' }
                    $sha = [BitConverter]::ToString(([Security.Cryptography.SHA256]::Create()).ComputeHash($bytes)).Replace('-','').ToLowerInvariant()
                    $utf8 = New-Object Text.UTF8Encoding($false, $true)
                    $result = @{path=$a.path;text=$utf8.GetString($bytes);sha256=$sha}
                }
                'file.list' {
                    $entries = @(Get-ChildItem -LiteralPath $a.path | Select-Object -First 1000 | ForEach-Object { @{name=$_.Name;is_dir=$_.PSIsContainer} })
                    $result = @{entries=$entries}
                }
                'file.write' {
                    $path = [string]$a.path
                    if (Test-Path -LiteralPath $path -PathType Leaf) {
                        if (-not $a.expected_sha256) { throw 'expected_sha256 required for overwrite' }
                        $old = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
                        if ($old -cne $a.expected_sha256) { throw 'File content changed since read' }
                    } elseif ($null -ne $a.expected_sha256) { throw 'File does not exist for supplied hash' }
                    [IO.File]::WriteAllText($path,[string]$a.text,(New-Object Text.UTF8Encoding($false)))
                    $result = @{path=$path;sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()}
                }
                'file.delete' {
                    Remove-Item -LiteralPath $a.path -ErrorAction Stop
                    $result = @{deleted=$a.path}
                }
                'process.run' {
                    $exe = [string]$a.executable
                    $argv = @($a.argv)
                    $working = [string]$a.cwd
                    if ($working) { Push-Location -LiteralPath $working }
                    try {
                        $output = (& $exe @argv 2>&1 | Out-String)
                        $code = $LASTEXITCODE
                    } finally { if ($working) { Pop-Location } }
                    if ($code -ne 0) { throw "Process failed with exit code $code : $output" }
                    $result = @{returncode=0;stdout=$output;stderr=''}
                }
                default { throw "Unsupported operation $($req.operation)" }
            }
            $out.ok=$true
            $out.result=$result
        } catch {
            $out.error_type='RemoteAgentCommandError'
            $out.error=$_.Exception.Message
        }
        $json = $out | ConvertTo-Json -Depth 12 -Compress
        Invoke-RestMethod -Uri "$Server/remote/agent/$Session/result" -Method Post -Headers $headers -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($json)) | Out-Null
    }
} finally {
    try {
        Invoke-RestMethod -Uri "$Server/remote/agent/$Session" -Method Delete -Headers $headers -TimeoutSec 5 | Out-Null
    } catch {
        Write-Warning "Unable to revoke session over network: $($_.Exception.Message)"
    }
}
