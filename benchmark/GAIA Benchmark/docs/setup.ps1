# Windows PowerShell: GAIA 평가 전용 환경 및 데이터 설치
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$adapter = Join-Path $root 'adapter'
$venv = Join-Path $adapter '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv $venv
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv $venv
    } else {
        throw 'Python 3.11 이상 설치가 필요합니다.'
    }
    if ($LASTEXITCODE -ne 0) { throw 'GAIA 평가용 가상환경 생성 실패' }
}
& $python -m pip install --disable-pip-version-check --no-input --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip 업데이트 실패' }
& $python -m pip install --disable-pip-version-check --no-input 'datasets>=4,<5' 'huggingface_hub>=0.34' 'httpx>=0.27'
if ($LASTEXITCODE -ne 0) { throw 'GAIA 평가 의존성 설치 실패' }
Write-Host 'Hugging Face GAIA 접근 약관 동의 및 로그인 후 이어서 진행하세요.'
Write-Host '접근 주소: https://huggingface.co/datasets/gaia-benchmark/GAIA'
& $python -m huggingface_hub.commands.huggingface_cli --help *> $null
# 인증은 기존 HF CLI 또는 HF_TOKEN 환경변수로 수행한다.
# 인증되지 않았으면 snapshot_download에서 명시적 오류를 반환한다.
& $python (Join-Path $adapter 'download_gaia.py')
if ($LASTEXITCODE -ne 0) { throw 'GAIA 데이터 다운로드 실패: 접근 권한 및 HF_TOKEN 확인' }
Write-Host 'GAIA 다운로드 완료. docs/README.md에서 실행 명령을 확인하세요.'
