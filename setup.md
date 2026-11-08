# MAI 설치·설정·검증 (Windows / Ubuntu 24.04)

현재 MAI는 **llama.cpp 전용**이다. Ollama 서버·SDK·모델 저장소는 설치하거나 시작하지 않는다. Python 패키지의 단일 의존성 선언은 `pyproject.toml`이며, Python·uv·llama.cpp·Chromium 바이너리 및 GPU 드라이버는 운영체제별 설치 과정에서 관리한다.

## 1. 저장소 준비

Git으로 MAI의 `dev`를 받은 뒤 저장소 루트에서 아래 명령을 실행한다. `main` 반영은 사용자가 검증 이후 수동으로 진행한다.

```bash
git clone --branch dev https://github.com/FoggyCaligo/MAI_MyAI_sllm.git
cd MAI_MyAI_sllm
./mai.sh setup
./mai.sh check
./mai.sh start
```

`mai.sh`는 Windows Git Bash/MSYS에서 PowerShell `mai.ps1`을 호출하고, Linux에서는 자체 Linux Portable 준비·실행 경로를 사용한다. Windows PowerShell의 대응 명령은 `.\mai.ps1 setup`, `.\mai.ps1 check`, `.\mai.ps1 start`다.

### 최초 준비 과정

1. `portable/portable-sources.json`(Windows) 또는 `portable/portable-sources-linux.json`(Linux)이 없다면, 운영체제별 릴리스 조회 스크립트로 **배포자가 제공한 SHA-256**을 기록한다. 기존 매니페스트는 덮어쓰지 않는다.
2. 해시를 대조한 Python 3.11·uv·llama.cpp 실행 파일을 설치한다. Windows는 `portable/portable-runtimes/`에, Ubuntu 24.04 x86-64는 호스트 캐시에 설치한다. Windows에서 최초 매니페스트 생성용 Python이 없다면 `winget`으로 Python 3.12를 설치하려고 시도하며, 실패하면 명시적 오류로 종료한다.
3. `pyproject.toml`의 기본 의존성과 `dev` 선택 의존성을 설치한다. 원격 워커는 **`[dependency-groups].remote`만** 설치한다. 본체와 워커 사이에 별도 `requirements.txt` 목록은 없다.
4. Playwright Chromium을 설치한다. GPU 드라이버·Tailscale·Cloudflare·OS 권한은 별도 호스트 조건이다.
5. `.env`가 없으면 Owner 로그인 ID·비밀번호를 입력받아 생성하고 체험 계정을 비활성화한다. 기존 `.env`는 보존한다.

Windows `setup`은 `mai.ps1` 내부, Linux `setup`은 `mai.sh` 내부에 구현되어 있다. 과거 `scripts/install_windows.ps1`, `portable/prepare-portable*.ps1|sh`는 사용하지 않는다.

## 2. GGUF 모델 준비

`settings.yaml`은 비밀이 아닌 모델·실행 기본값을 보관하고 `.env`에는 기기별 보안 정보와 재정의값을 보관한다.

```yaml
LLM_BACKEND: llamacpp
LLAMACPP_HOST: "http://127.0.0.1:8080"
LLAMACPP_GGUF_DIRECTORY: "portable/portable-models/gguf"
LLAMACPP_MAIN_GGUF_PATH: "portable/portable-models/gguf/Ornith-1.5-9B-Q4_K_M.gguf"
LLAMACPP_MAIN_MMPROJ_PATH: "portable/portable-models/gguf/mmproj-Ornith-1.5-9B-BF16.gguf"
LLAMACPP_MAIN_VISION: true
LLAMACPP_REASONING_PRESERVE: false
MODEL_MAX_OUTPUT_TOKENS: 32768
MODEL_REQUEST_TIMEOUT_SECONDS: 500
MAI_ANSWER_QUALITY_REVIEW: false
MAI_VERIFIER_BLOCKING_MODE: critical
```

모델과 projector 파일은 공식 배포처에서 라이선스를 확인하여 별도로 다운로드한다. `setup`은 모델 파일을 자동으로 추측하거나 다른 모델로 대체하지 않는다. GGUF 디렉터리 직접 자식 파일을 시작 시 카탈로그로 생성하며 기본 모델 경로가 실제 목록에 있어야 한다. UI 선택 모델을 Main·Verifier·기억 추출에 공통 사용한다. 모델 목록 갱신에는 서버 재시작이 필요하다.

Windows llama.cpp 실행기는 기본 컨텍스트 `131072`, 단일 상주 모델 `--models-max 1`을 사용한다. Linux 실행기도 128K 컨텍스트 프리셋을 사용한다. 128K가 실제 GPU에서 안정적이라는 보장은 없으며 메모리 사용량을 실기기에서 확인해야 한다.

## 3. 계정·외부 접속·원격 워커

`.env`의 `OWNER_USERS`와 `TRIAL_USERS`가 계정 권한을 결정한다. 계정 DB·감사 기록·원격 실행 키는 개인 정보다. 기본 로컬 웹 포트는 8000이다.

`REMOTE_HOSTING_PROVIDER`는 `tailscale`, `cloudflare`, `none` 중 하나이며 외부 웹 공개에만 영향을 준다. [원격 호스팅](docs/remote-hosting.md)과 [Native 원격 PC 연결](docs/remote-native-worker.md)은 별개 기능이다. 원격 워커는 별도 설치 환경의 Python 패키지를 `pyproject.toml`의 `remote` 그룹에서 설치한다.

## 4. 테스트와 오류 확인

```bash
./mai.sh check
./portable/portable-runtimes/python/python.exe -m pytest -q
```

두 번째 명령은 **Windows Git Bash** 기준이다. Linux에서는 해당 호스트 캐시의 Python을 사용해야 한다. `pytest` 실행 명령이 PATH에 없으면 `python -m pytest`를 사용한다. `No module named ...`는 의존성 설치 오류이며 무시하거나 테스트를 skip하지 않는다.

실기기에서 Windows/Linux Shell, GGUF 모델 적재·언로드, Native 도구 호출, Verifier 구조화 응답, 이미지 입력, SQLite 백업 및 원격 워커 인증을 별도로 확인해야 한다.

## 5. 백업 및 업데이트

GGUF 가중치·런타임 바이너리·사용자 `.env`·`data/*.sqlite3`·업로드는 Git에서 제외한다. 업데이트 전에 서비스를 종료하고 SQLite 일관성 백업과 매니페스트 해시를 보존한다. 런타임 URL 변경은 새 SHA-256 검증 뒤 진행하고, 실패를 기존 환경으로 자동 fallback하여 숨기지 않는다.

[Portable 상세](portable/README.md), [실행 구조](docs/architecture.md), [GPU와 OS 경계](docs/platform-boundary.md)를 참고한다.
