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
```
#### 2. 각 명령어 별 설명
**git clone --branch dev https://github.com/FoggyCaligo/MAI_MyAI_sllm.git**
MAI 프로젝트의 깃허브 레포를 클론(다운로드)한다.
**cd MAI_MyAI_sllm**
클론한 레포에 해당하는 폴더로 이동한다.
**./mai.sh setup** 
MAI에 필요한 기타 구성요소들을 자동으로 다운로드하도록 묶은 설치스크립트를 실행한다.( 다운로드 하는 모든 목록은 3번에서 상세하게 설명한다.)
`setup`은 시스템 RAM 용량과 관계없이 기본 Ornith 1.5 9B Q4_K_M 및 mmproj를 `portable/portable-models/gguf/ornith_1.5_9b_Q4/`에 자동 설치한다. 두 파일의 SHA-256을 검증하고 정상 파일은 재사용하며 손상된 파일을 덮어쓰지 않는다. Gemma 4는 별도 선택 가능한 모델로 유지하며 필요한 경우 `portable/install_default_model.py --gemma`로 설치할 수 있다.

#### 3. 설치 스크립트가 하는 것
- Python 3.11, uv, llama.cpp 다운로드 (공식 릴리스 URL·배포자 SHA-256에 근거)
- 기본 Ornith 1.5 9B Q4_K_M 및 mmproj 다운로드 (RAM 용량 무관, 공식 Hugging Face SHA-256 검증); Gemma 4는 선택적 다운로드
- 프로젝트 루트에 있는 `pyproject.toml`의 기본·개발 의존성, Playwright Chromium 다운로드
- .env 없으면 아이디/비번 입력받아 생성
- 네이티브 실행환경 준비 (windows : 프로젝트 폴더에서 ./portable/portable-runtimes.  linux : 호스트의 `~/.cache/mai-portable/` 또는 `XDG_CACHE_HOME` 아래)
 
**개인 DB 저장소 준비:** MAI는 기본적으로 프로젝트 루트 `mai/mcp_tools/mcp_memorizer/MAI_MEMORY/`의 DB를 사용한다. 별도 비공개 저장소를 처음 설치할 때 아래 명령으로 체크아웃한다. 기존에 보유한 `mai/mcp_tools/mcp_memorizer/MAI_MEMORY/`가 있으면 다시 복제하거나 덮어쓰지 않는다.

```bash
git clone https://github.com/FoggyCaligo/MAI_MEMORY.git mai/mcp_tools/mcp_memorizer/MAI_MEMORY
```

저장소 이름 변경 이전의 개인 `.env`에는 `data/` 또는 `MAI_MyAI_sllm_Memory/` 경로가 남아 있을 수 있으므로 [데이터 이전 안내](docs/memory-repository.md)를 참고해 경로를 직접 확인한다.

## 2. 외부 API 및 Hugging Face 인증 설정 (선택)

로컬 GGUF 모델만 사용한다면 외부 API 토큰이 필요하지 않다. OpenAI, Claude(Anthropic), OpenRouter API를 사용하거나 Hugging Face에서 접근 제한 데이터셋(GAIA 등)을 다운로드하려면 **프로젝트 루트의 `.env`에 각 서비스의 토큰을 직접 설정**한다. `./mai.sh setup`으로 만들어진 기존 `.env`에 필요한 항목만 추가하면 된다.

```dotenv
# OpenAI API (유료 사용량이 발생할 수 있음)
OPENAI_API_KEY=YOUR_OPENAI_API_KEY
MAI_OPENAI_MODELS=gpt-4.1

# Anthropic Claude API
ANTHROPIC_API_KEY=YOUR_ANTHROPIC_API_KEY
MAI_ANTHROPIC_MODELS=claude-sonnet-4-5

# OpenRouter API
OPENROUTER_API_KEY=YOUR_OPENROUTER_API_KEY
MAI_OPENROUTER_MODELS=openai/gpt-4.1

# Hugging Face: GAIA 등 접근 제한 데이터 다운로드용 읽기 토큰
HF_TOKEN=YOUR_HUGGING_FACE_READ_TOKEN

# 외부 API 출력 토큰 제한
CLOUD_MAX_OUTPUT_TOKENS=8192
```

위의 모델 ID는 **예시**이며, 실제로 사용 가능한 정확한 모델 ID로 변경한다. API 모델은 해당 API 키와 `MAI_<PROVIDER>_MODELS` 허용 목록을 모두 지정해야 모델 선택 목록에 나타난다. 외부 API를 사용할 때는 모델이 질문·도구 결과·첨부 이미지를 해당 서비스로 전송할 수 있으며 이용 요금이 발생할 수 있다. 로컬 llama.cpp가 기본값이며 오류 발생 시 외부 API로 자동 전환하지 않는다.

Hugging Face에서는 [Access Tokens](https://huggingface.co/settings/tokens)에서 읽기 권한의 토큰을 발급한다. GAIA 데이터셋은 [접근 조건](https://huggingface.co/datasets/gaia-benchmark/GAIA)에 별도로 동의해야 한다. `benchmark/GAIA Benchmark/adapter/download_gaia.py`가 루트 `.env`의 `HF_TOKEN`을 자동으로 읽으므로 별도의 터미널 토큰 입력은 필요하지 않다. 일반 공개 GGUF 파일 다운로드에는 보통 HF 토큰이 필요하지 않다.

**보안:** 실토큰을 `settings.yaml`, `.env.example`, 소스코드, 로그, Git 커밋에 저장하지 않는다. `.env`는 Git 추적에서 제외한다. 설정 우선순위는 **프로세스 환경변수 → `.env` → `settings.yaml`의 비밀값이 아닌 기본설정**이다. `.env` 변경 후에는 MAI 서버를 재시작한다.

세부 사항: [외부 모델 Provider](docs/cloud-model-providers.md), [GAIA 설치](benchmark/GAIA%20Benchmark/docs/README.md).

## 3. 모델 추가 다운받는 방법
기본 다운로드 모델 출처: https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF/tree/main (Gemma 4 선택적 다운로드: https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf/tree/main)

1. 허깅페이스에서, llama.cpp이 지원하는 모델 중, 본인의 컴퓨터에서 가동 가능한 모델을 찾는다. 링크 : https://huggingface.co/models?apps=llama.cpp
2. 모델을 찾았으면 클릭해서 상세 페이지로 이동, **Files** 탭으로 이동한다.
3. Files 탭 내부의 검색창에서 ".gguf"를 검색한다.
4. 원하는 모델 GGUF를 다운로드하고, 해당 모델에 맞는 mmproj GGUF가 있다면 함께 다운로드한다. 서로 다른 양자화 이름이어도 모델 전용 폴더에 같이 넣으면 된다.
5. **모델마다 별도 폴더**를 만든다. 예를 들어 `./portable/portable-models/gguf/Gemma-4-E4B-Q8/` 안에 모델 GGUF 1개와 mmproj GGUF 최대 1개를 넣는다. 폴더 안에 모델 GGUF가 2개 있으면 오류가 난다.
6. Git Bash에서 프로젝트 폴더로 이동 후, " **./mai.sh check** " 를 복사/붙여넣기/엔터 입력하여 모델 인식 상태를 확인한다.

### 기존 평면 GGUF 폴더에서 이전하기

기존 GGUF 파일은 다운로드할 필요 없이 **MAI가 종료된 상태에서** 모델별 폴더로 이동한다. 기존 데이터를 삭제하거나 덮어쓰지 말고 이동 대상의 파일이 없는지 먼저 확인한다. 아래는 Git Bash 예시이며 사용자 폴더에 실제로 존재하는 파일만 이동한다.

```bash
mkdir -p portable/portable-models/gguf/{Ornith-1.5-9B,Gemma-4-E4B-Q4,Gemma-4-E4B-Q8,Gemma-4-12B,LFM2.5-2.6B}
move_gguf() {
  local source="$1" destination="$2" target="$2/${1##*/}"
  if [[ ! -f "$source" ]]; then echo "파일 없음: $source" >&2; return 1; fi
  if [[ -e "$target" ]]; then echo "이미 존재함(덮어쓰기 거부): $target" >&2; return 1; fi
  mv -- "$source" "$destination/"
}
move_gguf "portable/portable-models/gguf/Ornith-1.5-9B-Q4_K_M.gguf" "portable/portable-models/gguf/Ornith-1.5-9B/"
move_gguf "portable/portable-models/gguf/mmproj-Ornith-1.5-9B-BF16.gguf" "portable/portable-models/gguf/Ornith-1.5-9B/"
move_gguf "portable/portable-models/gguf/gemma-4-E4B-it-Q4_K_M.gguf" "portable/portable-models/gguf/Gemma-4-E4B-Q4/"
move_gguf "portable/portable-models/gguf/mmproj-gemma-4-E4B-it-BF16.gguf" "portable/portable-models/gguf/Gemma-4-E4B-Q4/"
move_gguf "portable/portable-models/gguf/gemma-4-E4B-it-Q8_0.gguf" "portable/portable-models/gguf/Gemma-4-E4B-Q8/"
move_gguf "portable/portable-models/gguf/mmproj-gemma-4-E4B-it-Q8_0.gguf" "portable/portable-models/gguf/Gemma-4-E4B-Q8/"
move_gguf "portable/portable-models/gguf/gemma-4-12b-it-qat-q4_0.gguf" "portable/portable-models/gguf/Gemma-4-12B/"
move_gguf "portable/portable-models/gguf/mmproj-gemma-4-12b-it-qat-q4_0.gguf" "portable/portable-models/gguf/Gemma-4-12B/"
move_gguf "portable/portable-models/gguf/LFM2.5-2.6B-Q4_K_M.gguf" "portable/portable-models/gguf/LFM2.5-2.6B/
```
"
`active.gguf` 및 `.cache/`는 모델 폴더로 옮기지 않는다. 기본 모델의 새 경로는 `settings.yaml`에 반영되어 있다. 수동 이동을 완료하지 않고 시작하면 기본 모델을 찾지 못하므로 명시적으로 실패한다. `./mai.sh check`로 먼저 점검하고 재시작한다.

## 4. 실행시키는 방법
```bash
./mai.sh check
./mai.sh start
```
`check`는 필수 실행 파일과 모델 카탈로그를 검사한다. 서버 시작 시 `LLAMACPP_MAIN_GGUF_PATH`에 지정된 Ornith를 기본 GGUF로 선택하며, 사용자가 이후 모델 선택 UI에서 다른 모델로 변경하는 것은 자유롭다. 선택된 모델 파일이 없거나 손상되면 다른 모델로 자동 우회하지 않고 오류를 표시한다. `start`는 llama.cpp 라우터(기본 `127.0.0.1:8080`)와 MAI 웹 서버를 실행한다. 브라우저 접속 주소는 **http://127.0.0.1:8000**이다. 종료는 해당 실행 터미널에서 `Ctrl+C` 또는 `./mai.sh end`를 사용한다.

| 환경 | 설치 | 실행 |
|---|---|---|
| Windows Git Bash | `./mai.sh setup` | `./mai.sh start` |
| Windows PowerShell | `.\mai.ps1 setup` | `.\mai.ps1 start` |
| Windows CMD | `mai.cmd setup` | `mai.cmd start` |
| Ubuntu 24.04 Bash | `./mai.sh setup` | `./mai.sh start` |

루트의 `mai/`는 Python 패키지 디렉터리이므로 `./mai` 실행 파일은 없다. Ollama 전용 시작기와 별도 prepare 스크립트는 현재 코드에서 제거됐다. 모델·GPU·Verifier·도구 연동은 실행 후 별도로 확인한다.

## 5. llama.cpp 업데이트 방법

- 먼저 MAI 종료. Windows Git Bash / Linux Bash에서 실행:
```bash
./mai.sh update
./mai.sh check
./mai.sh start
```
- **업데이트 대상:** llama.cpp 실행 파일만. Python·uv·GGUF·개인 설정·DB는 변경하지 않음.
- **자동 처리:** 최신 호환 공식 릴리스 조회 → 다운로드(로컬 압축파일 우선) → 게시자 SHA-256 검증 → 실행 확인 → 기존 런타임·매니페스트 백업 후 교체.
- **다운로드 실패 시:** [llama.cpp 공식 릴리스](https://github.com/ggml-org/llama.cpp/releases)에서 오류 메시지에 나온 **정확한 파일명**의 압축파일을 받아 `portable/portable-downloads/`에 놓고 `./mai.sh update` 재실행. 해시 불일치 파일은 사용하지 않음.

## 6. 고급설정
### 1. 설정파일
- `settings.yaml` : 모델 최적화용 옵션 설정
- `.env` : 계정 ID·비밀번호, OpenAI/Anthropic/OpenRouter API 키 및 Hugging Face의 `HF_TOKEN` 등 인증정보
- 우선순위 :  **프로세스 환경변수 → `.env` → `settings.yaml`**

| 설정 | 기능 |
|---|---|
| `LLAMACPP_GGUF_DIRECTORY` | 모델별 하위 폴더를 탐색할 상위 폴더 |
| `LLAMACPP_MAIN_GGUF_PATH` | 기본 GGUF |
| 모델 전용 폴더의 `mmproj-*.gguf` | 해당 모델 프리셋의 이미지 입력용 projector 자동 연결 |
| `LLAMACPP_REASONING_PRESERVE` | llama.cpp 추론 보존 여부 |
| `MODEL_MAX_OUTPUT_TOKENS` | 모델 출력 토큰 한도(기본 32768) |
| `MODEL_REQUEST_TIMEOUT_SECONDS` | **각 llama.cpp 모델 호출** 제한 시간(기본 500초, 전체 사용자 요청 시간 한도가 아님) |
| `MAI_VERIFIER_BLOCKING_MODE` | `critical` 또는 `strict` |
| `MAI_VERIFIER_ENABLED` | 요청별 최종답변 검증 기본값(현재 `false`) |
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
