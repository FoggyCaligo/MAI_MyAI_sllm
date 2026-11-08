# MAI 설치 및 사용 안내

## 1. 초기 세팅

### 필요사항
1. Git 
2. 인터넷 연결

### 대상
1. Windows : Git Bash·PowerShell
2. Linux : Ubuntu 24.04 x86-64 터미널

### 방법
0. Git, Git Bash 검색하여 설치

#### 1. 아래 실행 명령어 묶음 복사 후, 터미널을 열어 붙여넣은 후 엔터를 누른다. 
```bash
git clone --branch dev https://github.com/FoggyCaligo/MAI_MyAI_sllm.git
cd MAI_MyAI_sllm
./mai.sh setup
mkdir -p portable/portable-models/gguf
curl -fL --retry 3 "https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF/resolve/main/Ornith-1.5-9B-Q4_K_M.gguf" -o portable/portable-models/gguf/Ornith-1.5-9B-Q4_K_M.gguf
curl -fL --retry 3 "https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF/resolve/main/mmproj-Ornith-1.5-9B-BF16.gguf" -o portable/portable-models/gguf/mmproj-Ornith-1.5-9B-BF16.gguf
```
#### 2. 각 명령어 별 설명
**git clone --branch dev https://github.com/FoggyCaligo/MAI_MyAI_sllm.git**
MAI 프로젝트의 깃허브 레포를 클론(다운로드)한다.
**cd MAI_MyAI_sllm**
클론한 레포에 해당하는 폴더로 이동한다.
**./mai.sh setup** 
MAI에 필요한 기타 구성요소들을 자동으로 다운로드하도록 묶은 설치스크립트를 실행한다.( 다운로드 하는 모든 목록은 3번에서 상세하게 설명한다.)
**mkdir -p portable/portable-models/gguf**
로컬llm모델을 설치할 폴더를 만든다.
**curl -fL --retry 3 "https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF/resolve/main/Ornith-1.5-9B-Q4_K_M.gguf" -o portable/portable-models/gguf/Ornith-1.5-9B-Q4_K_M.gguf**
ornith-1.5:9b 이라는 로컬모델을 다운로드 받는다.
**curl -fL --retry 3 "https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF/resolve/main/mmproj-Ornith-1.5-9B-BF16.gguf" -o portable/portable-models/gguf/mmproj-Ornith-1.5-9B-BF16.gguf**
ornmith-1.5:9b 모델이 이미지를 받을 수 있도록 돕는 ornith용 mmproj를 다운받는다.

#### 3. 설치 스크립트가 하는 것
- Python 3.11, uv, llama.cpp 다운로드 (공식 릴리스 URL·배포자 SHA-256에 근거)
- 프로젝트 루트에 있는 `pyproject.toml`의 기본·개발 의존성, Playwright Chromium 다운로드
- .env 없으면 아이디/비번 입력받아 생성
- 네이티브 실행환경 준비 (windows : 프로젝트 폴더에서 ./portable/portable-runtimes.  linux : 호스트의 `~/.cache/mai-portable/` 또는 `XDG_CACHE_HOME` 아래)
 
## 2. 모델 추가 다운받는 방법
기본 다운로드 모델 출처: https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF/tree/main

1. 허깅페이스에서, llama.cpp이 지원하는 모델 중, 본인의 컴퓨터에서 가동 가능한 모델을 찾는다. 링크 : https://huggingface.co/models?apps=llama.cpp
2. 모델을 찾았으면 클릭해서 상세 페이지로 이동, **Files** 탭으로 이동한다.
3. Files 탭 내부의 검색창에서 ".gguf"를 검색한다.
4. 나오는 파일들 중, 선택한 모델과 이름이 일치하는 파일 중 원하는 파일을 다운로드 한다. (**mmoproj**로 시작하는 .gguf 파일이 있다면 그것도 같이 다운로드 한다)
5. 다운로드한 모델 파일을 MAI 프로젝트의 다음 폴더에 넣는다. ./portable/portable-models/gguf/
6. Git Bash에서 프로젝트 폴더로 이동 후, " **./mai.sh check** " 를 복사/붙여넣기/엔터 입력하여 모델 인식 상태를 확인한다.

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

MAI를 완전히 종료한 뒤 llama.cpp 실행 파일만 업데이트하려면 다음을 실행한다.

```bash
./mai.sh update
./mai.sh check
./mai.sh start
```

Windows PowerShell은 `.\\mai.ps1 update`, CMD는 `mai.cmd update`를 사용한다. 업데이트는 공식 llama.cpp GitHub 릴리스에 게시된 SHA-256을 확인하고, 교체 전 새 실행 파일의 `--version` 실행을 검사한다. 기존 llama.cpp 런타임과 설치 매니페스트는 날짜별 백업으로 보존하며 실패 시 원상 복구를 시도한다. Python, uv, GGUF, SQLite는 변경하지 않는다. 동일한 릴리스라면 아무것도 변경하지 않는다.

실제 GPU 모델 추론은 `check`만으로 보장되지 않으므로 `start` 이후 별도로 시험한다.

소스 코드 업데이트는 llama.cpp 업데이트와 별개다. `git status`, `git switch dev`, `git pull --ff-only origin dev` 순으로 진행한 뒤 필요할 때 `./mai.sh setup`을 실행한다. 개인 .env·DB·GGUF는 백업하고, 충돌하는 로컬 변경 사항을 강제로 삭제하지 않는다.






## 5. 고급설정
### 1. 설정파일
- `settings.yaml` : 모델 최적화용 옵션 설정
- `.env` : 계정 아이디/비번 및 토큰 등의 개인정보.
- 우선순위 :  **프로세스 환경변수 → `.env` → `settings.yaml`**

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
