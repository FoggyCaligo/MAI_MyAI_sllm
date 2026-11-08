# MAI 설치 및 사용 안내

MAI는 **llama.cpp 전용** 로컬 AI 에이전트다. Windows Git Bash와 Ubuntu 24.04 x86-64에서는 `./mai.sh`로 실행한다. Python 패키지 의존성은 `pyproject.toml`에서 관리하며, GGUF 모델은 별도로 다운로드한다.

## 1. 초기 세팅

Git과 인터넷 연결이 필요하다. Windows는 Git Bash·PowerShell, Linux는 Ubuntu 24.04 x86-64를 대상으로 한다. GPU 드라이버와 운영체제의 기본 도구는 설치 환경에 준비되어 있어야 한다.

```bash
git clone --branch dev https://github.com/FoggyCaligo/MAI_MyAI_sllm.git
cd MAI_MyAI_sllm
./mai.sh setup
```

`setup`은 공식 릴리스 URL·배포자 SHA-256에 근거한 설치 매니페스트를 생성하거나 기존 것을 사용하여 Python 3.11, uv, llama.cpp를 준비한다. 이어 `pyproject.toml`의 기본·개발 의존성과 Playwright Chromium을 설치한다. `.env`가 없다면 Owner 로그인 ID·비밀번호를 받아 생성하고, 기존 `.env`는 덮어쓰지 않는다.

Windows는 `portable/portable-runtimes/`, Linux는 호스트의 `~/.cache/mai-portable/` 또는 `XDG_CACHE_HOME` 아래에 네이티브 실행 환경을 준비한다. 일반 setup은 관리자 권한으로 GPU 드라이버를 변경하지 않는다. Ubuntu에서 AMD/Intel Mesa Vulkan 패키지 설치를 **명시적으로 요청**할 때만 아래 옵션을 사용한다.

```bash
./mai.sh setup --install-vulkan
```

Windows PowerShell에서는 `.\mai.ps1 setup`, CMD에서는 `mai.cmd setup`을 사용할 수 있다. 설치기에는 GGUF 모델 자동 다운로드 기능이 없으므로 다음 단계가 필요하다.

## 2. 모델 다운받는 방법

MAI는 `portable/portable-models/gguf/` 폴더의 직접 자식 GGUF를 시작 시 탐색한다. 기본 설정은 Ornith 1.5 9B Q4_K_M과 비전 projector를 가리킨다.

모델 출처: https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF/tree/main

Windows Git Bash 또는 Linux Bash에서 프로젝트 루트 기준으로 다음을 실행한다.

```bash
mkdir -p portable/portable-models/gguf
curl -fL --retry 3 "https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF/resolve/main/Ornith-1.5-9B-Q4_K_M.gguf" -o portable/portable-models/gguf/Ornith-1.5-9B-Q4_K_M.gguf
curl -fL --retry 3 "https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF/resolve/main/mmproj-Ornith-1.5-9B-BF16.gguf" -o portable/portable-models/gguf/mmproj-Ornith-1.5-9B-BF16.gguf
```

해당 파일명은 Ornith 개발사의 공개 GGUF 저장소와 현재 `settings.yaml`을 대조했다. 다운로드 실패 파일은 정상 모델로 취급하지 말고 원본 출처·크기·SHA-256을 별도로 확인한다. `./mai.sh check`는 GGUF 헤더와 경로를 확인하지만 전체 무결성이나 실제 추론 성공까지 보증하지 않는다.

다른 GGUF를 기본 모델로 선택하려면 `settings.yaml`의 `LLAMACPP_MAIN_GGUF_PATH`를 바꾼다. 텍스트 전용 모델이라면 `LLAMACPP_MAIN_MMPROJ_PATH`를 제거하거나 주석 처리하고 `LLAMACPP_MAIN_VISION: false`로 설정한다. 다른 모델에 Ornith projector를 임의로 연결하지 않는다.

추가 GGUF를 같은 폴더에 두면 **서버를 재시작한 뒤** 모델 목록에 표시된다. Main·Verifier·기억 추출은 UI에서 선택한 동일 GGUF를 사용한다.

## 3. 실행시키는 방법

```bash
./mai.sh check
./mai.sh start
```

`check`는 필수 실행 파일과 모델 카탈로그를 검사한다. `start`는 llama.cpp 라우터(기본 `127.0.0.1:8080`)와 MAI 웹 서버를 실행한다. 브라우저 접속 주소는 **http://127.0.0.1:8000**이다. 종료는 해당 실행 터미널에서 `Ctrl+C`를 사용한다.

| 환경 | 설치 | 실행 |
|---|---|---|
| Windows Git Bash | `./mai.sh setup` | `./mai.sh start` |
| Windows PowerShell | `.\mai.ps1 setup` | `.\mai.ps1 start` |
| Windows CMD | `mai.cmd setup` | `mai.cmd start` |
| Ubuntu 24.04 Bash | `./mai.sh setup` | `./mai.sh start` |

루트의 `mai/`는 Python 패키지 디렉터리이므로 `./mai` 실행 파일은 없다. Ollama 전용 시작기와 별도 prepare 스크립트는 현재 코드에서 제거됐다. 모델·GPU·Verifier·도구 연동은 실행 후 별도로 확인한다.

## 4. 업데이트하는 방법

**먼저 MAI와 llama.cpp를 종료하고** `.env`, 개인 SQLite 데이터베이스(`data/`), 업로드, GGUF, 설치 매니페스트를 안전하게 백업한다. 소스 업데이트는 다음 순서다.

```bash
git status
git switch dev
git pull --ff-only origin dev
./mai.sh setup
./mai.sh check
```

`git status`에서 미반영 작업을 확인한다. 병합 충돌·변경 파일이 있으면 강제로 삭제하거나 리셋하지 말고 해결한다. `dev`에서 `main`으로 병합하는 과정은 자동으로 수행하지 않는다.

소스 갱신, llama.cpp/Python 바이너리 교체, GGUF 교체는 별개다. 새 바이너리를 쓰려면 공식 배포자 SHA-256을 확인해 OS별 개인 매니페스트를 명시적으로 갱신한다. 설치 영수증과 파일이 불일치하면 setup은 오류를 드러내며 임의로 덮어쓰지 않는다. 모델 교체는 새 GGUF를 별도로 검증한 후 `settings.yaml` 기본 경로를 변경하고 재시작한다.

## 5. 고급설정

비밀이 아닌 설정은 `settings.yaml`, 계정·토큰 및 개인 재정의는 `.env`에 둔다. 우선순위는 **프로세스 환경변수 → `.env` → `settings.yaml`**이다.

| 설정 | 기능 |
|---|---|
| `LLAMACPP_GGUF_DIRECTORY` | 모델 목록을 탐색할 폴더 |
| `LLAMACPP_MAIN_GGUF_PATH` | 기본 GGUF |
| `LLAMACPP_MAIN_MMPROJ_PATH`, `LLAMACPP_MAIN_VISION` | 비전 projector와 이미지 입력 |
| `LLAMACPP_REASONING_PRESERVE` | llama.cpp 추론 보존 여부 |
| `MODEL_MAX_OUTPUT_TOKENS` | 모델 출력 토큰 한도(기본 32768) |
| `MODEL_REQUEST_TIMEOUT_SECONDS` | 요청 제한 시간(기본 500초) |
| `MAI_VERIFIER_BLOCKING_MODE` | `critical` 또는 `strict` |
| `MAI_ANSWER_QUALITY_REVIEW` | 선택적 품질 검토(기본 `false`) |
| `SESSION_HISTORY_MESSAGES` | 최근 대화 메시지 수(기본 10) |
| `MAX_INLINE_TOOL_RESULT_CHARS` | 모델에게 보여주는 도구 결과 크기 |
| `REMOTE_HOSTING_PROVIDER` | `tailscale` / `cloudflare` / `none` |

Windows llama.cpp 런처는 기본 컨텍스트 **131072**, GPU 레이어 요청 **99**, 최대 상주 모델 수 **1**을 사용한다. 실제 GPU에서 128K를 안정적으로 처리할 수 있다는 보장은 없다. Windows 상세 실행 인자는 `portable/start-portable-llamacpp-router.ps1`, Linux 프리셋은 `portable/start-portable-llamacpp-linux.sh`에서 확인한다.

Windows Git Bash에서 전체 Python 테스트:

```bash
./portable/portable-runtimes/python/python.exe -m pytest -q
```

Linux에서는 `./mai.sh setup`에서 설치한 호스트 캐시 Python으로 `-m pytest -q`를 실행한다. 테스트 수집 오류, 필수 의존성 누락, 실행 실패를 성공으로 취급하지 않는다.

추가 정보: [Portable 안내](portable/README.md), [GGUF 모델 선택](docs/llamacpp-gguf-model-selection.md), [원격 워커](docs/remote-native-worker.md), [Verifier](docs/verifier.md).
