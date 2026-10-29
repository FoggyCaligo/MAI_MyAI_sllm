# MAI 설치 및 설정 가이드 (Windows)

MAI를 처음 설치하는 사용자를 위한 문서임. **1장 공통 준비 → 2장 설치 방식 하나 선택 → 3장 선택형 고급 설정** 순서로 진행함. Python·Ollama가 이미 설치되어 있는지와 관계없이, Portable 방식은 별도의 저장 공간에 런타임을 구성함.

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

`.env`에서 `OWNER_USERS`의 `user_id`, `user_pw`를 반드시 개인 값으로 수정하고, `db_id`는 이미 데이터를 사용 중이라면 유지함. `TRIAL_USERS`는 사용하지 않을 때 `[]`로 지정하고, `TAILSCALE_FUNNEL=false`로 시작할 것. 실제 비밀번호를 저장소·로그·공개 게시글에 노출하지 말 것.

**설정 우선순위:** 기존 환경변수와 `.env`의 동일 이름 값이 `settings.yaml` 기본값보다 우선함. 기존 `.env`의 `MAIN_MODEL` 또는 타임아웃 값을 YAML에서 변경해도 적용되지 않으면 `.env`에 동일 키가 있는지 확인할 것. `.env`를 덮어쓰지 않고 이관하려면 해당 키만 직접 삭제함. `settings.yaml`의 중복/알 수 없는 키와 부적절한 값 형식은 명시적 오류로 처리됨.

### 1.3 settings.yaml 설정 구분

**A. 정상 실행을 위한 연결 설정:** `LLM_BACKEND`(기본 Ollama), `OLLAMA_HOST`/ `LLAMACPP_HOST`(선택한 백엔드에 해당하는 주소), `MAIN_MODEL`(사용할 수 있는 모델). 각각 기본값이 들어 있지만 실제 사용 가능한 런타임 및 모델과 맞아야 함. `VISION_MODEL`은 이미지 인식에만 필요한 선택 설정이며, 전체 텍스트 MAI 실행의 필수 요건은 아님.

**B. 동작·성능 조정 설정:** `OLLAMA_REQUEST_TIMEOUT_SECONDS`, `OLLAMA_THINK`, `MAI_DEBUG_MODEL_INPUT`, `AGENT_MAX_IDENTICAL_CALLS`, `AGENT_MAX_IDENTICAL_FAILURES`, `AGENT_MAX_NO_PROGRESS_ROUNDS`, `TOOL_TIMEOUT_SECONDS`, `TERMINAL_TIMEOUT_SECONDS`, `MAX_INLINE_TOOL_RESULT_CHARS`, `MEMORY_RECALL_INCLUDE_UTTERANCES`, `SESSION_HISTORY_MESSAGES`, `MAI_ANSWER_QUALITY_REVIEW`, `VOICE_*` 중 음성 동작 파라미터. 값을 잘못 지정하면 오류가 날 수 있으므로 '임의 값이어도 무조건 실행됨'이라는 의미가 아님. 기본값을 유지하면 수동 입력은 필요하지 않음.

## 2. 기본 설치: 아래 두 방식 중 하나만 선택

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

4. `settings.yaml` 또는 기존 `.env`에서 지정한 모델을 다운로드. 기본 두 모델을 사용한다면 다음과 같음.

```powershell
ollama pull ornith-1.5:9b
ollama pull gemma4:e4b
ollama list
```

5. 실행 후 `http://127.0.0.1:8000` 접속.

```powershell
python run_server.py
```

자동 설치를 선호하면 `scripts/install_windows.ps1`을 확인한 뒤 사용할 수 있음. 이 스크립트는 시스템 Git·Python·Ollama 설치 및 초기 `.env` 생성을 자동화함. **이미 `.env`가 있으면 초기 계정 재설정을 하지 않음.**

### 2-B. Portable 설치 — Ollama 미설치 PC 및 외장 SSD

**전제:** 처음 준비하는 Windows PC에 Ollama가 설치되어 있지 않음. 프로그램 자체는 외장 SSD의 `portable-runtimes/`에 배치됨. 그러나 최초 준비를 위한 Git, PowerShell 및 Python 런처는 필요하며, 다운로드 시 인터넷이 필요함. 외장 SSD는 프로젝트 운영 정책상 NTFS를 사용하고, 포맷 시 모든 기존 데이터가 삭제되므로 **포맷 대상/백업 확인 후 Windows 파일 탐색기에서 사용자가 수동 포맷**해야 함. 설치 스크립트는 디스크를 포맷하지 않음.

1. **1장 공통 절차를 외장 SSD의 프로젝트 폴더에서 실행**하고, SSD에서 `settings.yaml`과 `.env`를 설정함.
2. 별도 PC Python이 필요한 최초 매니페스트 생성: `py --version` 또는 `python --version`으로 사용 가능한 Python 확인. Python이 없다면 먼저 설치해야 함(이는 Portable 런타임 준비 과정의 부트스트랩 요구사항임). Git Bash에서:

```bash
python scripts/resolve_portable_sources.py
```

이 명령은 공식 배포 자산 URL 및 SHA-256을 기록한 `portable-sources.json`을 생성하며, **기본 모델 목록을 `ornith-1.5:9b`, `gemma4:e4b` 두 개로 지정함.** 이미 매니페스트가 존재하면 자동으로 덮어쓰지 않음. GitHub API 한도를 초과하거나 게시자가 검증 가능한 SHA-256을 제공하지 않으면 실패함.

3. **기존 Ollama 모델 복사 옵션을 사용하지 않고**, Portable Python/uv/Ollama 런타임 준비와 두 모델 다운로드를 실행함.

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File ./prepare-portable.ps1 -Manifest ./portable-sources.json -PullModels -InstallChromium
```

`-PullModels`는 외장 SSD의 Ollama 실행 파일을 서버로 시작하여 매니페스트의 **두 모델만** 내려받음. 별도의 Ollama가 PC에 설치되어 있을 필요가 없지만, 11434 포트 충돌이나 다운로드 오류가 있으면 설치가 명확히 실패함. `-CopyLocalModels`는 사용하지 않음.

4. 필요한 파일의 준비 상태와 두 모델의 설치가 끝난 것을 확인한 뒤 서버 실행.

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File ./start-portable.ps1 -Backend ollama
```

5. `http://127.0.0.1:8000`에서 로그인해 테스트. 종료한 뒤 SSD를 안전하게 분리. 다른 PC에서는 호환되는 Windows x64 환경, 드라이버, 파일 접근 권한과 포트 상태를 확인해야 함. **Portable이라고 GPU 드라이버까지 포함되거나 모든 PC에서 무설치 실행이 보장되는 것은 아님.**

현재 Windows Portable 실행 경로가 제공되며, Ubuntu/Linux는 별도 실험적 준비와 호스트 종속성이 필요함. 상세 진단/추가 백엔드 기능은 [Portable 참고 문서](setup_portable.md) 및 [Portable 기술 설명](docs/portable-windows.md)을 참고할 수 있음.

## 3. 고급 설정 (선택)

### 3.1 외부 접속 — Tailscale

Tailscale은 **일반 설치와 Portable 설치 중 하나가 정상 실행된 다음** 설정함.

1. [Tailscale 공식 설치](https://tailscale.com/download/windows) 후 계정에 로그인하고 장치를 등록함.
2. 먼저 외부 인터넷 공개 없이 자신의 Tailnet 내부에서만 접속할지 결정함. 외부 인터넷으로 공개하려면 Tailscale Funnel을 별도 승인·구성해야 함.
3. `.env`에서 `TAILSCALE_FUNNEL=true`로 변경하려면 공개 주소에 MAI 로그인 페이지가 노출될 수 있다는 점을 먼저 확인할 것. 로그인 계정에 강력한 비밀번호 설정.
4. MAI 서버를 재시작해 출력된 주소 및 Tailscale 상태를 확인. 접속 실패 시 Funnel의 승인·정책·포트·장치 로그인 상태를 점검하고, 불필요할 때는 `TAILSCALE_FUNNEL=false`로 되돌림.

Tailscale 자체의 로그인과 승인 없이 설정값만 바꿔도 연결된다고 가정하면 안 됨. 외부 공개는 개인 PC 및 파일 도구에 대한 공격 표면을 넓힘.

### 3.2 음성 기능

1. 일반 설치는 `pip install -e .`으로 음성 의존성을 설치함. Portable 설치 역시 런타임 준비 시 의존성 설치 절차를 거침.
2. 사용 중인 브라우저에서 마이크 접근 권한을 허용하고, OS 입력 장치와 브라우저 설정을 확인함.
3. 음성 입력/화자 구분에 필요한 목소리 등록을 MAI UI에서 진행. 마이크·모델 다운로드/초기화가 실패하면 명시적으로 점검함.
4. `settings.yaml`에서 `VOICE_SILENCE_SECONDS`, `VOICE_MIN_SPEECH_SECONDS`, `VOICE_MATCH_THRESHOLD`, `VOICE_WHISPER_MODEL` 값을 조정할 수 있음. `VOICE_PROFILE_DIR`은 기기별 데이터 경로이므로 `.env`에 남김.
5. 음성 답변은 브라우저의 음성 합성에 의존하므로 기기·브라우저별 음성 품질과 지원 언어가 다를 수 있음.

## 4. 실행 문제 진단 및 업데이트

```bash
python -m compileall -q mai scripts tests
python -m pytest -q
python scripts/audit_codebase.py
```

위 명령은 **Python 환경을 설치한 뒤** 실행함. 감사의 정적 사용처 후보는 곧 삭제 대상이 아니며, 테스트 실패가 있으면 오류를 숨기지 않고 그대로 확인함.

- 설치 중 버전/패키지 오류: 현재 사용 중인 Python 버전, 가상환경 활성화, `python -m pip install -e .` 성공 여부 확인.
- Ollama 연결 실패: 서버 실행, `OLLAMA_HOST`, 모델 이름 및 `ollama list` 확인.
- 모델 미존재: 사용할 모델이 실제 Ollama 모델 저장소에 설치됐는지 확인.
- 기존 설정과 YAML이 다르게 동작: `.env`의 동일 키 또는 환경변수 우선순위 확인.
- Portable 준비 오류: `portable-sources.json`의 제공자 SHA-256, 11434 포트, SSD 사용 권한 및 디스크 여유 공간 확인.
- MAI DB 백업: 먼저 서버와 모델·기억 저장 작업을 종료하고 `.env`, `settings.yaml`, `data/`를 포함한 백업을 다른 저장장치에 보존할 것.

## 5. 라이선스

MAI의 라이선스는 AGPL-3.0-only이며 최초 개발자는 신재용임. 자세한 사항은 [LICENSE](LICENSE), [NOTICE](NOTICE)를 참조.
