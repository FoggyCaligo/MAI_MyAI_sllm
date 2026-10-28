# MAI Portable 설치·실행 설명서 (Windows)

이 문서는 외장 SSD의 MAI를 Windows PC에서 준비하고 실행하는 절차를 설명함. **SSD 파일시스템은 프로젝트 운영 기준상 NTFS로 준비함.** 현재 Linux Portable 시작 스크립트는 구현되지 않았으며, NTFS 포맷만으로 Linux에서 실행되는 것은 아님.

## 1. 준비 사항

- Windows 10/11 x64, 외장 SSD(NTFS), 인터넷 연결(최초 다운로드 시), PowerShell, Git 및 Python 설치 준비가 필요함.
- GPU를 사용하려면 대상 PC의 그래픽 드라이버 및 실행 엔진 호환성이 필요함. 외장 SSD에 드라이버까지 포함되어 자동 적용되는 구조는 아님.
- **포맷은 데이터를 지움.** 포맷 전 MAI 서버·Ollama·설치 작업을 종료하고, SSD 전체를 별도 HDD에 백업함. 탐색기에서 SSD의 용량·이름·드라이브 문자를 확인한 뒤 NTFS로 포맷함. 백업을 복원했다면 `.env`, `data/`, `portable-models/` 등을 확인함.
- 기존 설치와 데이터가 있다면 `.env`, SQLite 데이터베이스 및 모델 폴더를 덮어쓰거나 삭제하지 말 것.

## 2. 저장 구조

```text
MAI_MyAI_sllm/
├── mai/                            # MAI 코드
├── .env                            # 로그인 및 실행 설정(공개 금지)
├── data/                           # 기억·대화·로그 SQLite DB
├── portable-sources.json           # 검증된 다운로드 URL 및 SHA-256 (직접 생성)
├── portable-downloads/            # 다운로드 아카이브 캐시
├── portable-runtimes/
│   ├── python/                     # Windows Python 및 Python 패키지
│   ├── uv/                         # 패키지 설치용 도구
│   ├── ollama/                     # Windows Ollama
│   ├── llamacpp/                   # Windows llama.cpp (선택)
│   └── playwright-browsers/        # Chromium (선택)
├── portable-models/
│   ├── ollama/                     # Ollama 모델
│   └── gguf/                       # GGUF 모델 (선택)
├── prepare-portable.ps1
└── start-portable.ps1
```

Windows 드라이브 문자는 PC에 따라 달라도 됨. Git Bash에서 현재 MAI 폴더로 이동한 후 아래 명령을 실행함. **Git Bash에서 PowerShell 스크립트는 파일명만 입력해 실행하지 않음.**

## 3. 다운로드 목록 생성

Windows의 `python` 실행 별칭이 실제 Python을 가리키지 않으면 `python --version`이 `Python`만 출력할 수 있음. 이 경우 사용 가능한 Python 런처 버전을 확인하고, 아래 예시처럼 `py`를 사용함.

```bash
py --version
py -3.14 scripts/resolve_portable_sources.py --include-llamacpp
ls -lh portable-sources.json
```

`py -3.14`는 **Python 3.14가 설치된 PC의 예시**임. 다른 버전이면 그 버전으로 실행함. 스크립트는 필요한 배포 파일 URL과 배포자가 제공한 SHA-256을 저장하며, 기존 `portable-sources.json`을 덮어쓰지 않음.

GitHub API에 `HTTP 403: rate limit exceeded`가 발생할 경우, GitHub CLI를 설치하고 로그인한 뒤 Git Bash에서 다음처럼 실행할 수 있음.

```bash
gh auth login
gh auth status
export GITHUB_TOKEN="$(gh auth token)"
py -3.14 scripts/resolve_portable_sources.py --include-llamacpp
unset GITHUB_TOKEN
```

`gh`가 없다면 설치하거나 비인증 API 한도가 갱신된 뒤 다시 시도함. 인증 토큰은 파일이나 Git 커밋에 넣지 말 것.

## 4. Portable 런타임 설치

Git Bash에서 다음 명령을 실행함.

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File ./prepare-portable.ps1 -Manifest ./portable-sources.json -CopyLocalModels -InstallChromium
```

- `-CopyLocalModels`: 현재 PC의 `OLLAMA_MODELS` 경로(없으면 사용자 프로필의 `.ollama/models`)를 SSD로 복사함. 복사 전 기존 Ollama의 모델 기록 작업을 종료할 것.
- `-InstallChromium`: Playwright가 사용하는 Chromium을 별도로 다운로드함.
- `-PullModels`: 로컬 모델 복사 대신 manifest의 모델을 다운로드할 때 선택하는 옵션임. 이 옵션에는 모델용 Ollama 서버 실행 조건이 필요함.
- `--include-llamacpp`: **목록 생성 스크립트에만** 전달하는 옵션임. 설치 스크립트 옵션이 아님.

런타임이 이미 설치되어 있고 출처 기록이 일치하면 `Reusing verified runtime`이라고 출력함. 설치 기록이 없는 **이전 설치본**은 자동으로 덮어쓰지 않음. 기존 폴더를 보존한 뒤 새로 설치하려는 경우에만 명시적으로 아래 옵션을 사용함.

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File ./prepare-portable.ps1 -Manifest ./portable-sources.json -CopyLocalModels -InstallChromium -PreserveAndReinstallExisting
```

이 옵션은 기존 런타임을 `.before-portable-...` 폴더로 이동해 보존함. 이미 설치 기록이 일치하는 런타임은 재사용하므로 강제 재설치 옵션이 아님. 다운로드·패키지 설치 중에는 출력이 잠시 없어도 작업이 실행 중일 수 있음.

### 필요한 모델만 선택해서 복사

기존 PC의 Ollama 모델 저장소에 필요한 모델이 이미 있다면 `-CopyLocalModels`과 `-Models`를 함께 사용함. **Git Bash에서 PowerShell 배열 인수가 확실하게 전달되도록 쉼표 구분 문자열을 인용**함.

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File ./prepare-portable.ps1 -Manifest ./portable-sources.json -CopyLocalModels -Models "ornith-1.5:9b","gemma4:e4b","lfm2.5:8b"
```

이 명령은 지정된 3개 모델의 manifest와 참조된 SHA-256 blob만 복사하며, 다른 모델의 파일은 복사하지 않음. 공유 blob은 한 번만 복사하고 실제 파일 크기와 해시를 검증함. 대상에 파일이 이미 있다면 검증 후 재사용하지만, 다른 manifest나 손상된 blob은 자동 덮어쓰지 않고 명시적 오류로 처리함. **Portable Ollama를 포함한 모델 저장소 쓰기 프로세스를 모두 종료한 후 실행할 것.** 원본 모델 디렉터리는 수정하지 않음. 기존 빈 `portable-models/ollama` 폴더가 있어도 선택 복사 가능함.

`-Models` 없이 `-CopyLocalModels`만 사용하면 기존과 동일하게 전체 모델 디렉터리 복사를 시도하고, 대상이 이미 존재할 때는 덮어쓰지 않고 실패함. `-Models`는 `-CopyLocalModels`과만 조합하며 `-PullModels`와 함께 사용할 수 없음.

선택 복사 이후 Portable Ollama를 실행하고 별도의 Git Bash 창에서 확인함.

```bash
./portable-runtimes/ollama/ollama.exe list
```

모델 목록은 실행 중인 Ollama 서버의 저장소를 조회함. 따라서 Portable Ollama가 실행 중인지 먼저 확인할 것.

## 5. 실행

먼저 `.env`가 존재하는지, `portable-models/ollama/`에 사용할 모델이 있는지 확인함. 이후 Git Bash에서:

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File ./start-portable.ps1 -Backend ollama
```

이 스크립트는 SSD의 Python·Ollama·DB·모델 경로를 지정하고 MAI 서버를 시작함. 성공하면 브라우저에서 **http://127.0.0.1:8000**에 접속함. 이미 다른 Ollama가 `11434` 포트를 점유하고 있다면 포트 충돌로 명시적 오류가 발생함.

다음 명령은 별도로 설치된 Ollama를 사용하는 일반 실행이며, **Portable 전체 설정을 대신하지 않음**.

```bash
./portable-runtimes/python/python.exe run_server.py
```

llama.cpp 백엔드는 독립 GGUF 파일과 모델 별칭이 준비된 경우에만 다음처럼 사용함.

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File ./start-portable.ps1 -Backend llamacpp -GgufPath ./portable-models/gguf/model.gguf -ModelAlias local-model
```

모델 파일 경로와 별칭은 실제 환경에 맞게 수정해야 함.

## 6. 설치 오류 진단

| 증상 | 의미 및 조치 |
|---|---|
| `python --version`이 `Python`만 출력 | Windows 실행 별칭 문제 가능. `py --version` 확인 또는 SSD Python의 절대경로 사용 |
| `start-portable.ps1: command not found` | Git Bash에서 직접 실행하지 말고 `powershell -File ./start-portable.ps1` 사용 |
| `/usr/bin/tar: Cannot connect to D:` | 오래된 설치 스크립트가 Git Bash의 tar를 호출한 문제. 최신 `dev` 코드를 가져와 재시도 |
| `Refusing to overwrite` 또는 설치 기록 누락 | 기존 폴더 보존 후 재설치 옵션을 검토. 임의 삭제하지 말 것 |
| Chromium 설치 중 `__dirlock` 오류 | Playwright의 설치 잠금 갱신 실패. 다른 설치 프로세스 및 디스크 파일시스템·응답 상태 확인 |
| `Failed to hardlink ... falling back to full copy` | uv가 일반 복사로 전환했다는 경고. 그 자체가 설치 실패는 아님 |
| 모델 디렉터리가 이미 존재하여 복사 실패 | 기존 모델 데이터와 복사 완료 여부를 점검. 불완전한 복사본을 정상으로 간주하지 말 것 |

Chromium만 다시 설치하려면 다른 Playwright 설치가 종료된 것을 확인한 후 다음 명령 사용.

```bash
powershell -NoProfile -Command '$env:PLAYWRIGHT_BROWSERS_PATH=(Join-Path (Get-Location).Path "portable-runtimes\playwright-browsers"); & "./portable-runtimes/python/python.exe" -m playwright install chromium'
```

**Chromium 설치가 실패해도 Python 패키지 설치가 완료됐을 수 있음.** 설치 실패를 숨기지 말고 별도로 해결해야 함.

## 7. 이동·백업 및 Linux 지원 범위

- 다른 Windows PC로 이동 전 MAI와 Ollama를 종료하고 SSD를 안전하게 분리함.
- 외장 HDD 백업은 MAI 중지 후 **전체 폴더를 날짜별로 복사**하면 됨. `.env`에는 인증정보가 있으므로 백업 디스크 접근을 보호함.
- SQLite의 DB 복사 시점에는 쓰기 프로세스를 모두 종료해야 함.
- 현재 제공되는 Portable 설치·시작 스크립트는 **Windows 전용**임. NTFS는 Windows·Linux 양쪽에서 읽을 수 있지만, Linux의 실행 권한·마운트 옵션·시스템 라이브러리·GPU 드라이버 등의 별도 검증이 필요함. **Linux에서 SSD 연결만으로 바로 MAI 실행하는 기능은 아직 구현되지 않았음.**

추가 설명: [기존 Portable 디스크 준비 문서](setup-portable.md), [Windows Portable 기술 문서](docs/portable-windows.md).
