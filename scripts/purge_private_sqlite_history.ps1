# Requires Git and git-filter-repo; run from a PowerShell 5.1+ terminal.
# Rewrites ALL branch/tag history. Every prior commit SHA changes.
# Creates a full local mirror backup before any rewrite and never uploads it.
param(
    [switch]$Push
)
$ErrorActionPreference = 'Stop'
$repoUrl = 'https://github.com/FoggyCaligo/MAI_MyAI_sllm.git'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$work = Join-Path $env:TEMP "mai-private-db-purge-$stamp"
$backup = Join-Path $work 'original-backup.git'
$clean = Join-Path $work 'cleaned.git'
New-Item -ItemType Directory -Path $work -Force | Out-Null

function Invoke-Git {
    param([string[]]$Arguments)
    & git @Arguments
    if ($LASTEXITCODE -ne 0) { throw "git failed: $($Arguments -join ' ')" }
}

# Abort before touching any remote refs when tooling is unavailable.
Invoke-Git @('version')
Invoke-Git @('filter-repo', '--version')
Invoke-Git @('clone', '--mirror', $repoUrl, $backup)
Invoke-Git @('clone', '--mirror', $backup, $clean)

# Remove only private conversation / memory / audit databases, including
# their write-ahead-log and shared-memory sidecars, at ANY directory depth.
# Explicitly preserve sentence_breaker.sqlite3 and other quality datasets.
$pattern = '(^|/)(chat|memory|log)\.sqlite3(?:-(?:wal|shm))?$'
Invoke-Git @('-C', $clean, 'filter-repo', '--force', '--invert-paths', '--path-regex', $pattern)

# Verify that no reachable branch or tag exposes the excluded filenames.
$remaining = @(& git -C $clean rev-list --objects --all |
    Select-String -Pattern $pattern)
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect rewritten objects' }
if ($remaining.Count -gt 0) {
    throw "Verification failed: $($remaining.Count) private database paths remain"
}

foreach ($branch in @('main', 'dev')) {
    & git -C $clean cat-file -e "refs/heads/${branch}:data/sentence_breaker.sqlite3"
    if ($LASTEXITCODE -ne 0) {
        throw "Expected sentence_breaker database is missing from $branch"
    }
}

Write-Host "Rewritten history verified. Original backup: $backup"
Write-Host "Clean mirror ready: $clean"
if (-not $Push) {
    Write-Host 'Dry-run only. Run again with -Push to publish rewritten branches/tags.'
    exit 0
}

# git-filter-repo deliberately removes the remote; add it explicitly.
Invoke-Git @('-C', $clean, 'remote', 'add', 'origin', $repoUrl)
Invoke-Git @('-C', $clean, 'push', '--force', 'origin', 'refs/heads/*:refs/heads/*')
$tags = @(& git -C $clean for-each-ref --format='%(refname)' refs/tags)
if ($tags.Count -gt 0) {
    Invoke-Git @('-C', $clean, 'push', '--force', 'origin', 'refs/tags/*:refs/tags/*')
}
Write-Host 'Rewritten branch and tag refs pushed. Other clones must be re-cloned.'
Write-Host 'GitHub cached/unreachable objects, PR refs and forks may require separate remediation.'
