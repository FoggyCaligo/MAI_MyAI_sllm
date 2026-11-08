# MAI 설치 및 설정 가이드 (Windows / Ubuntu 24.04)

MAI를 처음 설치하는 사용자를 위한 문서. **1장 공통 설정 → 2장 실행 환경 준비 → 3장 고급 설정 → 4장 점검·업데이트** 순서로 진행한다. Windows와 Ubuntu 24.04는 OS별 네이티브 런타임이 필요하다. Portable의 모델·설정·데이터는 외장 SSD에 유지하되 Linux 실행 바이너리와 Python 패키지는 Linux 호스트 캐시에 설치한다. 현재 `dev`에는 llama.cpp와 Ollama 백엔드가 모두 있으며, 아래에서는 GGUF를 사용하는 llama.cpp Portable 경로를 우선 설명한다. 현재 기능과 OS별 제한은 [실행 구조](docs/architecture.md), [GGUF 선택](docs/llamacpp-gguf-model-selection.md), [원격 Native 워커](docs/remote-native-worker.md) 문서를 참고한다.

> 중요: 설치와 사용 중 파일 변경·삭제 또는 터미널 실행이 가능하므로 데이터 백업 권장. 설치 스크립트는 다운로드한 실행 파일을 설치·실행하므로 내용을 먼저 확인할 것. 비밀번호 및 인증 토큰을 Git에 올리지 말 것.

## 1. 공통 준비: GitHub 다운로드와 설정 파일

### 1.1 Git 및 저장소 가져오기

Windows PowerShell 또는 Git Bash를 열어 `git --version`으로 Git 설치를 확인함. 없으면 [Git for Windows](https://git-scm.com/downloads/win)에서 설치한 뒤 터미널을 다시 실행함. 일반 설치는 PC 내 폴더에서, Portable 설치는 **외장 SSD의 NTFS 볼륨**에서 아래 명령을 실행함. 다음은 Git Bash 예시임.

```bash
git clone --branch dev https://github.com/FoggyCaligo/MAI_MyAI_sllm.git
cd MAI_MyAI_sllm
```

기존 설치에서는 다시 clone하지 않고 해당 폴더에서 `git pull`로 업데이트함. 변경 전 개인 파일과 DB를 백업하고, 로컬 수정이 있는 경우 먼저 확인할 것.

### 1.2 설정 파일 두 개의 역할

| 파일 | 관리 대상 | 공개 여부 |
|---|---|---|
| `settings.yaml` | 모델/백엔드, 동작 정책, 시간제한, 음성 설정 등 비밀이 아닌 기본값 | Git 추적 |
| `.env` | 로그인 계정·비밀번호, 로컬 경로, 접속 주소, 서버 공개 설정 및 기기별 덮어쓰기 | **Git 추적 금지** |

`.env`가 없다면 **프로젝트 루트에서** 복사함.

PowerShell:

```powershell
Copy-Item .env.example .env
```

Git Bash:

```bash
cp .env.example .env
```

`.env`에서 `OWNER_USERS`의 `user_id`, `user_pw`를 반드시 개인 값으로 수정하고, `db_id`는 이미 데이터를 사용 중이라면 유지함. `TRIAL_USERS`는 사용하지 않을 때 `[]`로 지정할 것. 원격 공개 제공자 선택은 `settings.yaml`의 `REMOTE_HOSTING_PROVIDER`로 관리하며 기본값은 `tailscale`임. Tailscale이 설치·로그인·승인되어 있지 않다면 공개는 시작되지 않고 로컬 서버는 계속 실행됨. 실제 비밀번호를 저장소·로그·공개 게시글에 노출하지 말 것.

**Verifier 변경 시 확인:** 기존 `.env` 또는 프로세스 환경변수에 `VERIFIER_MODEL=granite4:3b-h`가 있으면 YAML 기본값을 바꿔도 그대로 적용됨. `LiquidAI/lfm2.5-2.6b:q4_k_m`를 사용하려면 해당 값을 수정하거나 기존 덮어쓰기를 삭제하고 모델을 설치할 것.

**설정 우선순위:** 기존 환경변수와 `.env`의 동일 이름 값이 `settings.yaml` 기본값보다 우선함. 기존 `.env`의 `MAIN_MODEL` 또는 타임아웃 값을 YAML에서 변경해도 적용되지 않으면 `.env`에 동일 키가 있는지 확인할 것. `.env`를 덮어쓰지 않고 이관하려면 해당 키만 직접 삭제함. `settings.yaml`의 중복/알 수 없는 키와 부적절한 값 형식은 명시적 오류로 처리됨.

### 1.3 settings.yaml 설정 구분

**A. 정상 실행을 위한 연결 설정:** `LLM_BACKEND`(기본 Ollama), `OLLAMA_HOST`/ `LLAMACPP_HOST`(선택한 백엔드에 해당하는 주소), `MAIN_MODEL`(사용할 수 있는 모델). 각각 기본값이 들어 있지만 실제 사용 가능한 런타임 및 모델과 맞아야 함. `VERIFIER_MODEL`은 검증 전용 모델이며 기본값은 `LiquidAI/lfm2.5-2.6b:q4_k_m`다. 해당 키를 생략하면 기존처럼 본 모델로 검증한다. `VISION_MODEL`은 별도 이미지 인식 도구용 선택 설정이다. Ollama의 메인 모델에 `vision` capability가 있을 때 업로드 이미지 첨부는 메인 모델에 직접 전달되며, 그 외에는 기존 `VISION_MODEL`의 `image_analyze` 도구를 활용한다. 전체 텍스트 MAI 실행에는 이미지 모델이 필수는 아니다.

**Verifier 품질 검토:** `MAI_ANSWER_QUALITY_REVIEW=false`는 추가 응답 품질 검토를 비활성화하는 설정이다. 기본 사실·숫자 검증과는 다른 설정이며, 메인·Verifier 프롬프트를 전환하지 않는다. 기존 `.env`·환경변수에 `MAI_ANSWER_QUALITY_REVIEW=false`가 있으면 그 값이 우선하므로 변경 후 확인할 것.

**B. 동작·성능 조정 설정:** `OLLAMA_REQUEST_TIMEOUT_SECONDS`, `OLLAMA_AGENT_MAX_OUTPUT_TOKENS`, `AGENT_MAX_IDENTICAL_CALLS`, `AGENT_MAX_IDENTICAL_FAILURES`, `AGENT_MAX_NO_PROGRESS_ROUNDS`, `MAX_INLINE_TOOL_RESULT_CHARS`, `SESSION_HISTORY_MESSAGES`, `MAI_ANSWER_QUALITY_REVIEW`, `VOICE_*` 중 음성 동작 파라미터. 값을 잘못 지정하면 오류가 날 수 있으므로 '임의 값이어도 무조건 실행됨'이라는 의미가 아님. 기본값을 유지하면 수동 입력은 필요하지 않음.

현재 `OLLAMA_THINK`, `MAI_DEBUG_MODEL_INPUT`, `TOOL_TIMEOUT_SECONDS`, `TERMINAL_TIMEOUT_SECONDS`, `MEMORY_RECALL_INCLUDE_UTTERANCES`는 YAML 로더에서 허용하지만 실제 실행 경로에서 값을 읽지 않음. 이 키를 편집해도 해당 동작이 바뀌지 않음. Main adapter는 `think=True`이며 도구 timeout은 등록 함수의 인자로 정함.

| 실제 적용되는 항목 | 배포 `settings.yaml` 값 |
|---|---|
| 모델 요청 제한 | `OLLAMA_REQUEST_TIMEOUT_SECONDS: 500`초 |
| Ollama main 출력 한도 | `OLLAMA_AGENT_MAX_OUTPUT_TOKENS: 32768` |
| 한 번의 도구 결과 입력 한도 | `MAX_INLINE_TOOL_RESULT_CHARS: 8000`자 |
| 최근 대화 | `SESSION_HISTORY_MESSAGES: 10`개 메시지 |
| 선택적 품질 검토 | `MAI_ANSWER_QUALITY_REVIEW: false` |
| 음성 종료 무음 시간 / 화자 임계값 | `VOICE_SILENCE_SECONDS: 5` / `VOICE_MATCH_THRESHOLD: 0.65` |

기억 추출의 Ollama 출력은 기본 2,048토큰이며, 필요하면 **프로세스 환경변수 또는 `.env`**의 `OLLAMA_MEMORY_MAX_OUTPUT_TOKENS`로 지정함. 이 키는 현재 YAML 허용 목록에는 없음.

MAI는 Verifier의 컨텍스트 길이를 별도로 제한하지 않는다. 큰 컨텍스트에서는 GPU 메모리가 부족해 CPU 오프로딩이 발생할 수 있으므로 `ollama ps`를 확인한다. LFM2.5-2.6B는 메모리 부담을 낮추기 위한 기본 Verifier 모델이며, 기존 `.env` 또는 환경변수의 `VERIFIER_MODEL`이 우선한다.

## 2. 실행 환경 선택: 일반 설치 또는 Portable

### 2-A. 일반 설치 — Python과 Ollama를 PC에 설치

Windows 10/11을 기준으로 진행함.

1. [Python 공식 설치 안내](https://www.python.org/downloads/windows/)에서 Python 3.11 이상(호환되는 64-bit 버전) 설치. 새 터미널에서 `python --version` 확인. Windows 앱 실행 별칭 때문에 `python`이 동작하지 않으면 `py --version` 확인.
2. [Ollama](https://ollama.com/download/windows)를 Windows에 설치하고 `ollama --version`으로 확인. Ollama 서버가 실행 가능한지 확인.
3. 프로젝트 루트에서 Python 가상환경을 생성·활성화하고 필요한 패키지를 설치함.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m playwright install chromium
```

4. `settings.yaml` 또는 기존 `.env`에서 지정한 모델을 다운로드. 기본 세 모델을 사용한다면 다음과 같음.

```powershell
ollama pull ornith-1.5:9b
ollama pull LiquidAI/lfm2.5-2.6b:q4_k_m

ollama pull lfm3-vl:2b
ollama list
```

5. 실행 후 `http://127.0.0.1:8000` 접속.

```powershell
python run_server.py
```

자동 설치를 선호하면 `scripts/install_windows.ps1`을 확인한 뒤 사용할 수 있음. 이 스크립트는 시스템 Git·Python·Ollama 설치 및 초기 `.env` 생성을 자동화함. **이미 `.env`가 있으면 초기 계정 재설정을 하지 않음.**

### 2-B. 외장 SSD Portable 설치

준비·실행·모델 선택 복사·Ubuntu·백업은 [Portable 안내](portable/README.md) 한곳에서 관리한다. 일반 설치와 달리 `portable/` 아래 별도 실행 환경을 사용한다. 계정과 Tailscale·음성 설정은 아래 공통 안내를 따른다.


### 2-C. llama.cpp GGUF Portable — Windows와 Ubuntu 공통 설정

**현재 권장 Portable 실행 경로:** MAI 소스·설정·GGUF·DB는 동일한 SSD에 놓고, **Windows는 Windows 실행 파일**, **Linux는 Linux 실행 파일**로 llama.cpp 라우터를 실행한다. 이 실행 방식은 **서버 하나(127.0.0.1:8080)에서 메인·Verifier GGUF를 각각 라우팅**한다. 라우터의 최대 상주 모델 수는 **1로 고정**한다. Main의 도구 호출·임시답변 생성까지 Main이 유지되고, Verifier 호출 시 Main을 내리고 LFM을 로드한다. 검증 후 재수정이 필요하면 Main을 다시 적재한다. 실제 KV 파일의 저장/복원은 사용하지 않고 대화·도구 로그와 직전 임시답변 및 피드백을 새 모델 요청에 포함한다. 기존 Agent·Verifier 프롬프트 및 검증 흐름은 유지한다.

**모델 경로는 명령 인자 대신 프로젝트 루트의 `settings.yaml`에 둔다.** 현재 파일은 다음 구성을 가리킨다.

```yaml
LLAMACPP_MAIN_GGUF_PATH: "portable/portable-models/gguf/Ornith-1.5-9B-Q4_K_M.gguf"
LLAMACPP_MAIN_MMPROJ_PATH: "portable/portable-models/gguf/mmproj-Ornith-1.5-9B-BF16.gguf"
LLAMACPP_MAIN_VISION: true
LLAMACPP_VERIFIER_GGUF_PATH: "portable/portable-models/gguf/LFM2.5-2.6B-Q4_K_M.gguf"
```

상대 경로는 프로젝트 루트 기준이다. 메인 모델·projector의 조합이 실제로 이미지 입력을 처리하는지는 llama.cpp 버전과 모델로 확인해야 한다. **파일 경로·GGUF 헤더 검사만으로 비전 추론 성공은 증명되지 않는다.**

#### 2-C-1. Hugging Face에서 GGUF 내려받기 (실사용 예시)

모델 저장소 및 파일명은 각 제작자의 Hugging Face 파일 목록과 대조한다. 아래는 **Ornith 1.5 9B Q4_K_M 메인 모델, BF16 mmproj, LFM2.5 Q4_K_M Verifier**를 현재 `settings.yaml` 경로에 다운로드하는 예시다.

1. Python 3.11 이상이 설치된 인터넷 연결 PC에서 Hugging Face CLI 준비:

```bash
python -m pip install -U "huggingface_hub[cli]"
hf --help
```

2. 프로젝트 **루트**에서 다음 명령 실행 (Windows Git Bash 또는 Linux Bash):

```bash
hf download ornith-ai/Ornith-1.5-9B-GGUF Ornith-1.5-9B-Q4_K_M.gguf --local-dir "./portable/portable-models/gguf"
hf download ornith-ai/Ornith-1.5-9B-GGUF mmproj-Ornith-1.5-9B-BF16.gguf --local-dir "./portable/portable-models/gguf"
hf download LiquidAI/LFM2.5-2.6B-GGUF LFM2.5-2.6B-Q4_K_M.gguf --local-dir "./portable/portable-models/gguf"
```

공식 파일 확인: [Ornith 1.5 GGUF](https://huggingface.co/ornith-ai/Ornith-1.5-9B-GGUF/tree/main), [LFM GGUF](https://huggingface.co/LiquidAI/LFM2.5-2.6B-GGUF/tree/main). 접근 승인이 필요한 저장소에서는 `hf auth login` 후 이용 약관·라이선스 조건에 따라 다운로드한다. **다운로드 명령은 모델 파일만 가져오며, llama.cpp 바이너리나 MAI Python 패키지를 설치하지 않는다.** 저장 경로·파일명은 `settings.yaml`과 정확히 일치해야 한다.

3. 다운로드 확인:

```bash
ls -lh portable/portable-models/gguf/*.gguf
```

Windows PowerShell에서는 `Get-ChildItem ./portable/portable-models/gguf/*.gguf`를 쓴다. GGUF 전체 파일 크기, 디스크 공간, 배포자 파일 해시를 함께 확인하는 것이 좋다. **Main GGUF와 mmproj는 한 쌍으로 관리**한다. 모델 교체 때는 두 파일의 호환성을 다시 검증한다.

#### 2-C-2. Windows llama.cpp Portable 설치·실행

현재 설치기는 Ollama 지원을 병행하므로 **llama.cpp만 실행하더라도 준비 단계에서 Ollama CLI 및 일부 Ollama Python 패키지를 설치할 수 있다**. Ollama 프로그램을 **실행**할 필요는 없다. 이는 향후 단일 백엔드 정리와 구분되는 현재의 설치 동작이다.

Windows PowerShell에서 프로젝트 루트 기준:

```powershell
python portable/resolve_portable_sources.py --include-llamacpp
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/prepare-portable.ps1 -Manifest ./portable/portable-sources.json
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/start-portable-llamacpp-router.ps1
```

`portable-sources.json`이 이미 있으면 첫 번째 명령은 덮어쓰지 않고 실패한다. 기존 매니페스트를 보존한 후 새로 생성하거나 기존 매니페스트에 `llamacpp` 항목이 유효한지 점검한다. `-InstallChromium`은 브라우저 도구를 사용할 때 설치 명령에 추가한다. 메인과 Verifier의 GGUF가 준비되기 전에는 라우터가 기동되지 않는다.

#### 2-C-3. Ubuntu 24.04 Linux llama.cpp Portable 설치·실행

지원 기준은 **Ubuntu 24.04 x86-64**다. 외장 SSD에 동일한 저장소·GGUF·설정·DB를 유지하되 Linux 네이티브 Python·llama.cpp 실행 파일과 의존성은 **Linux PC의 호스트 캐시**에 준비한다. Windows의 `python.exe` 또는 `llama-server.exe`를 Linux에서 재사용하지 않는다.

먼저 인터넷이 되는 PC의 프로젝트 루트에서 Linux용 릴리스 URL·발행자 SHA-256을 고정한 매니페스트 생성:

```bash
python3 portable/resolve_portable_sources_linux.py --include-llamacpp
```

기존 `portable/portable-sources-linux.json`이 있다면 **덮어쓰지 않으므로**, 내용을 보존하고 갱신된 릴리스를 선택해야 한다. 이후 **실제 Ubuntu 24.04 대상 PC에서** 실행:

```bash
bash portable/prepare-portable-linux.sh --with-llamacpp
bash portable/start-portable-llamacpp-linux.sh
```

선택적 준비 옵션: `--install-vulkan`(호스트 AMD/Intel Mesa Vulkan 패키지), `--install-chromium`(Playwright 브라우저 파일). 시스템 드라이버·권한 및 추가 Chromium Linux 시스템 라이브러리가 필요할 수 있다. 준비 스크립트는 `check-linux-gpu.py --require` 검사에 실패하면 중단한다. 첫 실행은 호스트 캐시 준비를 필요로 하므로 **미준비 Linux PC에 SSD를 꽂는 것만으로 즉시 실행된다고 보장하지 않는다.**

**도구 지원 경계:** Linux 로컬 `terminal_run`은 `/bin/sh`를 사용하며 파일·코드·CSV·문서 도구는 OS 독립 Python 경로를 쓴다. 반면 **Linux를 원격 작업 대상 PC로 연결하는 워커는 아직 Windows PowerShell 전용**이다. 실제 Linux 브라우저·GPU 비전·음성·외장 SSD의 SQLite 쓰기 권한은 대상 PC에서 별도 점검한다. [Linux 도구 감사](docs/linux-tool-audit.md)를 참조한다.

#### 2-C-4. llama.cpp 업데이트 방법 (OS별 공통 원칙)

llama.cpp는 MAI 코드나 GGUF 파일과 **독립적으로 교체 가능한 네이티브 추론 엔진**이다. 안전한 갱신 순서는 **MAI 종료 → 기존 실행 환경과 매니페스트 백업 → 새 릴리스의 발행자 SHA-256으로 매니페스트 고정 → 새 바이너리 설치 → 실행·추론·비전·Verifier 테스트 → 실패 시 원복**이다. 진행 중인 MAI 서버/라우터를 종료하고 DB 백업부터 진행한다.

**Windows — 기존 런타임을 보존한 후 갱신**

1. `portable/portable-sources.json`을 백업한다. 기존 파일을 덮어쓰지 않고 보존한 뒤 아래 명령으로 새로운 매니페스트를 만든다.
2. 변경된 llama.cpp 릴리스는 기존 설치 영수증과 다르므로 **기존 `portable/portable-runtimes/llamacpp` 디렉터리를 다른 이름으로 옮겨 보존**해야 한다. 현재 설치기는 해시가 다른 기존 런타임을 자동 덮어쓰지 않는다.
3. 새 매니페스트로 설치기를 실행하고 `llama-server.exe`가 존재하는지 확인한다.

```powershell
# MAI 및 llama.cpp 라우터를 완전히 종료한 후
Copy-Item ./portable/portable-sources.json ./portable/portable-sources.previous.json
Move-Item ./portable/portable-sources.json ./portable/portable-sources.saved.json
python portable/resolve_portable_sources.py --include-llamacpp

# 현재 버전의 llama.cpp만 보존해두고 다른 런타임은 그대로 재사용
Move-Item ./portable/portable-runtimes/llamacpp ./portable/portable-runtimes/llamacpp.before-update
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/prepare-portable.ps1 -Manifest ./portable/portable-sources.json
./portable/portable-runtimes/llamacpp/llama-server.exe --version
```

`llamacpp.before-update`가 이미 있다면 다른 백업 이름을 지정한다. 기존 `portable-sources.saved.json`이 이미 있으면 안전한 다른 이름으로 보존한다. **매니페스트에 Python·uv·Ollama 출처까지 바뀌었다면** 현재 설치기는 해당 항목의 영수증 불일치도 거부하므로, 이 경우에는 기존 매니페스트의 **검증된 다른 런타임 항목을 유지하면서 llama.cpp 항목만 새 릴리스 값으로 교체**하는 편이 안전하다. 단, 출처 URL·SHA-256은 반드시 GitHub가 게시한 값이어야 한다. 모델 GGUF는 엔진 갱신 때문에 재다운로드할 필요는 없지만 호환성은 재검증한다.

**Ubuntu — 호스트 캐시의 Linux llama.cpp만 보존 후 갱신**

Linux 설치 준비 스크립트도 SHA-256이 다른 기존 llama.cpp 런타임을 자동 교체하지 않는다. 기존 매니페스트를 백업하고 새 URL/공식 해시로 `llamacpp` 항목을 갱신한 뒤, **호스트 캐시 안의 해당 llama.cpp 디렉터리만 백업**한다.

```bash
# MAI와 라우터 종료, 매니페스트의 새 llamacpp URL/SHA-256을 검증한 후
root="$(pwd -P)"
id="$(printf '%s' "$root" | sha256sum | cut -c1-24)"
cache_base="$(printf '%s' "$HOME/.cache")"
if [ -n "$XDG_CACHE_HOME" ]; then cache_base="$XDG_CACHE_HOME"; fi
cache="$cache_base/mai-portable/$id"
mv "$cache/llamacpp" "$cache/llamacpp.before-update"
bash portable/prepare-portable-linux.sh --with-llamacpp
"$cache/llamacpp/bin/llama-server" --version
```

일부 셸은 `XDG_CACHE_HOME` 미설정 시 빈 문자열을 사용하며, 사용자는 호스트별 권한과 설치 디렉터리를 확인해야 한다. `llamacpp.before-update`가 있으면 백업 이름을 변경한다. Linux 매니페스트의 Python·uv·Ollama 릴리스 항목도 바뀌면 해당 캐시의 영수증 불일치로 설치가 중단될 수 있으므로 원본 항목을 유지한 채 `llamacpp` 항목만 갱신한다.

**갱신 후 검사 (Windows/Linux 공통):** `/v1/models`에서 `mai-main`과 `mai-verifier`가 표시되는지, 실제 메인 응답·도구 호출·Ornith 이미지 입력·LFM Verifier JSON 구조화 검증이 성공하는지 확인한다. 모델 목록 등록만으로 추론 성공을 확정할 수 없다. 문제가 생기면 MAI 종료 후 이전 바이너리와 이전 매니페스트를 복원한다. llama.cpp 업데이트는 GGUF와 mmproj 형식을 새 버전이 지원하는지 확인하며 진행한다.

**현재 상태:** Windows 및 Linux 실행 스크립트가 별개인 이유는 OS 네이티브 바이너리·셸이 다르기 때문이다. llama.cpp를 공통 추론 백엔드로 통일하더라도 이 **OS별 런처 구분은 필요**하다. 사용자는 하나의 `setup.md`에서 통합 관리하고, 엔진과 GGUF 경로는 `settings.yaml` 및 Portable 매니페스트를 통해 관리할 수 있다.


## 3. 고급 설정 (선택)

### 3.1 외부 접속 — Tailscale(기본) / Cloudflare 선택

**원격 호스팅은 MAI 핵심 실행과 분리되어 있음.** `mai/hosting/`에서 제공자별 실행을 처리하며, 메인 모델·Verifier·기억·원격 PC 작업은 별개로 유지됨.

`settings.yaml`의 기본값은 다음과 같음.

```yaml
REMOTE_HOSTING_PROVIDER: tailscale    # tailscale | cloudflare | none
```

| 값 | 준비 조건 및 동작 |
|---|---|
| `tailscale` (기본) | 호스트에 Tailscale 설치·로그인·Funnel 허용 후 공개 URL 생성 |
| `cloudflare` | 호스트에 cloudflared 설치, Cloudflare Dashboard의 Named Tunnel·공개 호스트명 설정, `.env`의 `CLOUDFLARE_TUNNEL_TOKEN` 필요 |
| `none` | MAI 프로세스가 새로운 공개 터널을 실행하지 않음 |

Tailscale은 [공식 설치 안내](https://tailscale.com/download/windows)에서 설치하고 로그인한 뒤 Funnel 사용을 승인한다. `tailscale funnel status`로 상태를 확인한다. Cloudflare는 대시보드에서 관리형 터널과 `http://127.0.0.1:8000`을 대상으로 하는 공개 호스트명을 구성한 후 토큰을 `.env`에 추가한다. 비밀 토큰을 `settings.yaml`이나 Git에 올리지 않는다.

기존 `.env`에 `TAILSCALE_FUNNEL` 설정이 남아 있다면 구형 옵션이므로 제거할 수 있다. `REMOTE_HOSTING_PROVIDER`가 `.env`나 프로세스 환경변수에 있으면 YAML보다 우선하므로 전환 때 먼저 확인한다. Tailscale Funnel은 `--bg`로 유지되기 때문에 제공자를 바꿔도 **이미 등록된 Funnel이 자동 해제되지 않을 수 있음**. 다른 제공자로 전환한 후 `tailscale funnel status`에서 기존 공개 설정을 확인하고 필요한 경우 해당 규칙을 직접 해제해야 함.

호스팅 시작에 실패해도 로컬 MAI 서버는 계속 실행된다. `/health`의 `remote_hosting_provider`는 해당 제공자 실행이 시작됐음을 표시하며 실제 외부 HTTPS 연결의 성공 여부를 보장하지 않음. 다른 네트워크에서 MAI 로그인까지 확인할 것.

오너의 **원격 PC 작업 연결은 호스팅과 별도**이며, `dev`에서는 원격 작업 없이 10시간 경과 시 세션이 해제됨(초기 연결 10분, 에이전트 연결 확인 3분은 유지). 이 제한은 Tailscale/Cloudflare 공개 웹 터널을 끄는 기능이 아님.

설치·인증·제공자 전환·보안·진단의 상세 사항은 [한국어 원격 호스팅 설정](docs/remote-hosting.md)을 참고한다. 공개 웹 로그인은 터미널·파일 접근 권한이 있는 MAI에 대한 공격 표면을 넓히므로 오너 암호와 계정 권한을 반드시 점검한다.

### 3.2 음성 기능

1. 일반 설치는 `pip install -e .`으로 음성 의존성을 설치함. Portable 설치 역시 런타임 준비 시 의존성 설치 절차를 거침.
2. 사용 중인 브라우저에서 마이크 접근 권한을 허용하고, OS 입력 장치와 브라우저 설정을 확인함.
3. 음성 입력/화자 구분에 필요한 목소리 등록을 MAI UI에서 진행. 마이크·모델 다운로드/초기화가 실패하면 명시적으로 점검함.
4. `settings.yaml`에서 `VOICE_SILENCE_SECONDS`, `VOICE_MATCH_THRESHOLD`, `VOICE_WHISPER_MODEL` 값을 조정할 수 있음. 일반 음성 입력은 최소 발화 길이 제한 없이 음성 감지·화자 확인·음성 인식을 시도함. 최초 목소리 등록에는 3초 이상의 실제 발화가 필요함. 이전의 `VOICE_MIN_SPEECH_SECONDS` 설정은 제거함. `VOICE_PROFILE_DIR`은 기기별 데이터 경로이므로 `.env`에 남김.
5. 음성 답변은 브라우저의 음성 합성에 의존하므로 기기·브라우저별 음성 품질과 지원 언어가 다를 수 있음.

## 4. 실행 문제 진단 및 업데이트

### pytest 환경 확인

Git Bash/Portable 환경에서는 `pytest` 명령이 `PATH`에 없을 수 있다. 이 경우 **테스트를 실행할 Python 자체를 명시**한다.

```bash
./portable/portable-runtimes/python/python.exe -m pytest -q
```

`No module named pytest`는 해당 Python의 개발 의존성 누락이며, `No module named ollama`는 이 프로젝트의 필수 의존성 누락이다. 설치된 일반 Python과 Portable Python의 패키지를 혼동하지 않는다. 프로젝트의 의존성 목록은 `pyproject.toml`을 따른다. **테스트 수집 오류·SyntaxError·누락된 패키지를 테스트 통과로 간주하지 않는다.**


테스트를 실행하려면 일반 실행 의존성 외에 개발용 의존성(`pytest`, JSON 스키마 검증용 `jsonschema`)을 같은 Python 환경에 설치함.

```bash
python -m pip install -e ".[dev]"
python -m compileall -q mai scripts tests
python -m pytest -q
python scripts/audit_codebase.py
```

위 명령은 **Python 환경을 설치한 뒤** 실행함. 감사의 정적 사용처 후보는 곧 삭제 대상이 아니며, 테스트 실패가 있으면 오류를 숨기지 않고 그대로 확인함.

- 설치 중 버전/패키지 오류: 현재 사용 중인 Python 버전, 가상환경 활성화, `python -m pip install -e .` 성공 여부 확인.
- Ollama 연결 실패: 서버 실행, `OLLAMA_HOST`, 모델 이름 및 `ollama list` 확인.
- 모델 미존재: 사용할 모델이 실제 Ollama 모델 저장소에 설치됐는지 확인.
- 기존 설정과 YAML이 다르게 동작: `.env`의 동일 키 또는 환경변수 우선순위 확인.
- Portable 준비 오류: `portable/portable-sources.json`의 제공자 SHA-256, 11434 포트, SSD 사용 권한 및 디스크 여유 공간 확인.
- MAI DB 백업: 먼저 서버와 모델·기억 저장 작업을 종료하고 `.env`, `settings.yaml`, `data/`를 포함한 백업을 다른 저장장치에 보존할 것.

## 5. 라이선스

MAI의 라이선스는 AGPL-3.0-only이며 최초 개발자는 신재용임. 자세한 사항은 [LICENSE](LICENSE), [NOTICE](NOTICE)를 참조.

## 6. 체험 계정 초기화

서버를 종료하고 프로젝트 루트에서 실행한다. 실제 삭제 전 `--dry-run`으로 대상 계정과 DB·업로드 경로를 확인한다.

```powershell
python scripts/reset_trial.py 체험판 --dry-run
python scripts/reset_trial.py 체험판
```

`TRIAL_USERS`에 설정된 정확한 `user_id`만 받으며 안정적인 `db_id` 기준으로 해당 계정의 기억·대화·업로드를 초기화한다. 실제 실행에는 확인 입력이 필요하고 `--yes`로 생략할 수 있다. 경로를 별도 지정하려면 `--db`, `--chat-db`, `--upload-root`를 사용한다. 상대경로는 프로젝트 루트 기준이다.


## 부록: 128K llama.cpp 컨텍스트와 웹 검색 문맥 관리

Windows 및 Linux llama.cpp Portable 라우터의 기본 컨텍스트를 **131,072토큰(128K)**으로 설정한다. Ollama 앱의 Context length 슬라이더는 llama.cpp 설정에 영향을 주지 않는다. Windows에서 장비 메모리가 부족하면 `-ContextSize 32768` 등으로 실행별 하향할 수 있다.

Main의 도구 반복 도중 검색/웹페이지 원문을 단순 누적하지 않도록, **최초 출력 이후의 후속 모델 이력**에는 `web_search`의 원래 제목·URL·스니펫, `web_fetch`의 문서 URL·제목·본문 앞 2,200자 및 원문 조회 ID를 남긴다. 원본 결과는 감사 로그와 실행별 `ToolResultStore`에 보존하고, 나머지는 기존 `tool_result_read`로 조회할 수 있다. 이것은 **원문 발췌**이지 의미 요약·사실 검증·완전한 문서 분석은 아니다. [도구 결과 상세](docs/TOOL_RESULT_PAGINATION.md)를 참고한다.

128K는 VRAM 요구량을 증가시킬 수 있으며 16GB GPU에서 추론 성공을 보장하지 않는다. `--models-max 1`을 유지하더라도 KV 캐시와 비전 projector가 VRAM을 사용한다. 불안정하거나 GPU 오류가 발생하면 추론을 중단하고 컨텍스트를 줄여 재검증해야 한다.
