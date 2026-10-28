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
if ((Read-Host 'Type YES to authorize remote management') -cne 'YES') {
    throw 'Remote control was not authorized'
}
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
        $venvPython = Join-Path $target '.venv\\Scripts\\python.exe'
        if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
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
            New-Item -ItemType Directory -Force -Path $target | Out-Null
            Expand-Archive -LiteralPath $zipPath -DestinationPath $target -Force
            & $pythonCommand @pythonArgs -m venv (Join-Path $target '.venv')
            if ($LASTEXITCODE -ne 0) { throw 'Remote Python venv creation failed' }
            & $venvPython -m pip install --disable-pip-version-check --no-input -e $target
            if ($LASTEXITCODE -ne 0) { throw 'Remote worker dependency installation failed' }
        }
        $env:MAI_REMOTE_PYTHON = $venvPython
        $env:PYTHONPATH = $target
        Write-Host "MAI remote Python worker ready: $target"
    } finally {
        Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
    }
}

Initialize-MaiRemotePython
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
                'native.invoke' {
                    # The bootstrap must provision a Python environment containing MAI before
                    # this operation is dispatched. An absent worker is a real failure.
                    $python = [Environment]::GetEnvironmentVariable('MAI_REMOTE_PYTHON', 'Process')
                    if ([string]::IsNullOrWhiteSpace($python)) {
                        throw 'MAI_REMOTE_PYTHON is not configured; remote Python worker is unavailable'
                    }
                    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
                        throw "Remote Python executable is missing: $python"
                    }
                    $payload = @{ name = [string]$a.name; arguments = $a.arguments } | ConvertTo-Json -Compress -Depth 40
                    $psi = New-Object Diagnostics.ProcessStartInfo
                    $psi.FileName = $python
                    $psi.Arguments = '-m mai.remote.python_worker'
                    $psi.UseShellExecute = $false
                    $psi.RedirectStandardInput = $true
                    $psi.RedirectStandardOutput = $true
                    $psi.RedirectStandardError = $true
                    $psi.StandardInputEncoding = New-Object Text.UTF8Encoding($false)
                    $psi.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
                    $psi.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
                    $psi.CreateNoWindow = $true
                    $process = New-Object Diagnostics.Process
                    $process.StartInfo = $psi
                    if (-not $process.Start()) { throw 'Unable to launch remote Python worker' }
                    try {
                        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
                        $stderrTask = $process.StandardError.ReadToEndAsync()
                        $process.StandardInput.WriteLine($payload)
                        $process.StandardInput.Close()
                        if (-not $process.WaitForExit(110000)) {
                            $process.Kill()
                            throw 'Remote Python worker timed out after 110 seconds'
                        }
                        $stdout = $stdoutTask.GetAwaiter().GetResult()
                        $stderr = $stderrTask.GetAwaiter().GetResult()
                        if ($process.ExitCode -ne 0) {
                            throw "Remote Python worker failed (exit $($process.ExitCode)): $stderr"
                        }
                        $parsed = $stdout | ConvertFrom-Json
                        if ($null -eq $parsed.result) {
                            throw 'Remote Python worker returned no result'
                        }
                        $result = $parsed.result
                    } finally {
                        $process.Dispose()
                    }
                }
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
