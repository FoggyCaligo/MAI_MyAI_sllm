# 소스 비교를 위한 별도 Git 체크아웃. 설치·온보딩·실행은 하지 않음.
$ErrorActionPreference = 'Stop'
$agents = Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path 'agents'
New-Item -ItemType Directory -Force -Path $agents | Out-Null
$repositories = @(
    @{Name='openclaw'; Url='https://github.com/openclaw/openclaw.git'},
    @{Name='hermes-agent'; Url='https://github.com/NousResearch/hermes-agent.git'}
)
foreach ($repo in $repositories) {
    $target = Join-Path $agents $repo.Name
    if (Test-Path $target) {
        throw "대상 디렉터리가 이미 존재합니다. 기존 파일을 보호하기 위해 중단: $target"
    }
    & git clone --depth 1 $repo.Url $target
    if ($LASTEXITCODE -ne 0) { throw "다운로드 실패: $($repo.Name)" }
    & git -C $target rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw "체크아웃 식별 실패: $($repo.Name)" }
}
Write-Host "소스 체크아웃 완료: $agents"
Write-Host '실행 설치, 종속 패키지, 서버 기동은 수행하지 않았습니다.'
