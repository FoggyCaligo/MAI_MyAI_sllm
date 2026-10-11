# MAI Web UI 처음 실행하기

이 문서는 개발 환경을 거의 모르는 사람도 MAI Web UI를 켤 수 있도록, Python 설치부터 Ollama 모델 준비, `.env` 설정, Tailscale Funnel 공개 URL 설정까지 순서대로 설명한다.

가장 흔한 사용 흐름은 다음과 같다.

1. Python을 설치한다.
2. Ollama를 설치하고 모델을 내려받는다.
3. Tailscale을 설치하고 로그인한다.
4. 이 저장소를 내려받는다.
5. `.env` 파일을 만든다.
6. `python run_server.py`로 Web UI를 실행한다.
7. 브라우저에서 `http://127.0.0.1:8000` 또는 Tailscale Funnel URL로 접속한다.

---

## 1. 준비물

### 필수

- Windows 10/11, macOS, Linux 중 하나
- 인터넷 연결
- Python 3.11 이상
- Git
- Ollama

### 선택

- Tailscale
  - 같은 PC에서만 쓸 거면 없어도 된다.
  - 휴대폰이나 다른 PC에서 접속하거나, 공개 HTTPS URL을 만들려면 필요하다.

### 권장 사양

- RAM 16GB 이상
- 여유 저장 공간 20GB 이상
- GPU가 있으면 좋지만 필수는 아니다.

큰 모델은 느리거나 메모리 부족이 날 수 있다. 처음에는 가벼운 모델로 성공 여부를 확인한 뒤 바꾸는 편이 안전하다.

---

## 2. Python 설치

이 프로젝트는 `pyproject.toml` 기준으로 Python 3.11 이상이 필요하다.

### Windows

1. 공식 다운로드 페이지를 연다.
   - https://www.python.org/downloads/
2. 최신 Python 3.x Windows installer를 내려받는다.
3. installer 첫 화면에서 반드시 **Add python.exe to PATH**를 체크한다.
4. **Install Now**를 누른다.
5. 설치가 끝나면 PowerShell을 새로 연다.
6. 아래 명령으로 확인한다.

```powershell
python --version
pip --version
```

성공 예:

```text
Python 3.12.12
pip 26.x from ...
```

`python`을 찾을 수 없다고 나오면 PowerShell을 완전히 닫았다가 다시 열어본다. 그래도 안 되면 Python installer를 다시 실행해서 **Modify** 또는 재설치 과정에서 **Add python.exe to PATH**가 켜져 있는지 확인한다.

### macOS

Homebrew가 있다면 다음처럼 설치할 수 있다.

```bash
brew install python
python3 --version
pip3 --version
```

Homebrew가 없다면 공식 Python 다운로드 페이지에서 macOS installer를 받아 설치한다.

### Linux

Ubuntu/Debian 계열 예시:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
python3 --version
pip3 --version
```

다른 배포판은 패키지 관리자 이름만 다르다. 핵심은 `python3`, `venv`, `pip`, `git`이 필요하다는 점이다.

---

## 3. Git 설치와 저장소 받기

이미 이 저장소 폴더가 PC에 있으면 이 장은 건너뛰어도 된다.

### Windows

1. Git for Windows를 설치한다.
   - https://git-scm.com/download/win
2. 설치 옵션은 기본값으로 진행해도 된다.
3. PowerShell을 새로 열고 확인한다.

```powershell
git --version
```

4. 원하는 위치로 이동한 뒤 저장소를 받는다.

```powershell
cd $HOME\Documents\Git
git clone https://github.com/FoggyCaligo/MAI_MyAI_sllm.git
cd MAI_MyAI_sllm
```

### macOS / Linux

```bash
cd ~/Documents
git clone https://github.com/FoggyCaligo/MAI_MyAI_sllm.git
cd MAI_MyAI_sllm
```

이미 폴더가 있다면 그 폴더로 이동한다.

```bash
cd path/to/MAI_MyAI_sllm
```

---

## 4. Ollama 설치

MAI는 OpenAI API가 아니라 로컬 Ollama 서버에 붙어서 모델을 실행한다. Ollama가 켜져 있어야 Web UI가 답변을 만들 수 있다.

### Windows

1. Ollama 공식 다운로드 페이지를 연다.
   - https://ollama.com/download
2. Windows installer를 내려받아 설치한다.
3. 설치 후 Ollama 앱이 백그라운드에서 실행되는지 확인한다.
4. PowerShell을 새로 열고 확인한다.

```powershell
ollama --version
```

### macOS

1. https://ollama.com/download 에서 macOS 앱을 내려받아 설치한다.
2. 앱을 실행한다.
3. Terminal을 열고 확인한다.

```bash
ollama --version
```

### Linux

Ollama 공식 페이지의 Linux 설치 명령을 사용한다.

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama --version
```

설치 후 서비스가 자동으로 켜지지 않았다면 다음 중 하나로 시작한다.

```bash
ollama serve
```

또는 systemd 환경이면:

```bash
systemctl status ollama
```

---

## 5. Ollama 모델 내려받기

`.env.example`의 기본 모델은 다음과 같다.

```env
MAIN_MODEL=ornith-1.5:9b
VISION_MODEL=gemma4:12b
```

사용할 모델은 PC에 미리 받아져 있어야 한다. 먼저 현재 설치된 모델을 확인한다.

```bash
ollama list
```

원하는 모델이 없으면 내려받는다.

```bash
ollama pull ornith-1.5:9b
```

이미 다른 모델을 쓰고 싶다면 그 모델을 내려받고 `.env`의 `MAIN_MODEL` 값을 정확한 모델 이름으로 바꾼다.

예:

```bash
ollama pull llama3.1:8b
```

```env
MAIN_MODEL=llama3.1:8b
```

이미지 분석 도구 `image_analyze`까지 쓰려면 vision model도 필요하다.

```bash
ollama pull gemma4:12b
```

이미지 분석이 필요 없거나 PC가 버거우면 `.env`에서 `VISION_MODEL=`처럼 비워둔다. 그러면 `image_analyze` 도구는 Web UI 런타임에 등록되지 않는다.

모델이 정상인지 빠르게 확인하려면:

```bash
ollama run ornith-1.5:9b
```

짧은 질문을 입력해 답이 나오면 성공이다. 빠져나올 때는 `/bye`를 입력한다.

---

## 6. Python 가상환경 만들기

가상환경은 이 프로젝트 전용 Python 공간이다. 한 번 만들어두면 이후에는 실행 전에 활성화만 하면 된다.

### Windows PowerShell

저장소 폴더 안에서 실행한다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

PowerShell에서 스크립트 실행이 막혔다면 현재 창에서만 허용한 뒤 다시 활성화한다.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

활성화에 성공하면 프롬프트 앞에 `(.venv)`가 붙는다.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

설치 중 `sentence-breaker`를 GitHub에서 받아오므로 인터넷 연결이 필요하다.

---

## 7. `.env` 파일 만들기

`.env.example`은 예시 파일이고, 실제 실행에는 `.env`가 필요하다. 저장소 폴더에서 복사한다.

### Windows PowerShell

```powershell
Copy-Item .env.example .env
notepad .env
```

### macOS / Linux

```bash
cp .env.example .env
nano .env
```

처음에는 아래 항목만 먼저 확인하면 된다.

```env
OLLAMA_HOST=http://127.0.0.1:11434
MAIN_MODEL=ornith-1.5:9b
VISION_MODEL=

MEMORY_DB_PATH=./data/memory.sqlite3
SENTENCE_BREAKER_DB_PATH=./data/sentence_breaker.sqlite3
CHAT_DB_PATH=./data/chat.sqlite3

OWNER_USERS=[{"user_id":"owner","user_pw":"change-me","db_id":"local-user"}]
TRIAL_USERS=[{"user_id":"체험판","user_pw":"0000","db_id":"trial-default"}]

MAI_HOST=127.0.0.1
MAI_PORT=8000
SESSION_HISTORY_MESSAGES=12
TAILSCALE_FUNNEL=false
```

처음 실행은 `TAILSCALE_FUNNEL=false`로 두는 것을 권장한다. 로컬에서 잘 켜지는 것을 확인한 뒤 Tailscale을 켜면 문제를 찾기 쉽다.

반드시 바꿀 값:

- `OWNER_USERS`의 `user_pw`
  - 예시의 `change-me`를 실제 비밀번호로 바꾼다.
- `MAIN_MODEL`
  - `ollama list`에 보이는 모델 이름과 정확히 일치해야 한다.
- `VISION_MODEL`
  - 이미지 분석을 쓸 때만 설정한다.
  - 안 쓸 때는 빈 값으로 둔다.

`.env`에는 로그인 비밀번호가 평문으로 들어간다. Git에 올리면 안 된다.

---

## 8. 로컬에서 Web UI 실행

가상환경이 켜진 상태에서 실행한다.

```bash
python run_server.py
```

성공하면 터미널에 다음과 비슷한 줄이 보인다.

```text
MAI local: http://127.0.0.1:8000
MAI Tailscale Funnel: disabled (set TAILSCALE_FUNNEL=true to enable)
```

브라우저에서 연다.

```text
http://127.0.0.1:8000
```

로그인:

```text
ID: owner
PW: .env의 OWNER_USERS에 적은 user_pw
```

서버를 끄려면 터미널에서 `Ctrl+C`를 누른다.

---

## 9. 실행 확인 체크리스트

Web UI가 제대로 켜졌는지 아래 순서로 확인한다.

1. 브라우저에서 로그인 화면이 보인다.
2. `owner` 계정으로 로그인된다.
3. 짧은 메시지를 보낸다.
   - 예: `안녕, 한 문장으로 답해줘.`
4. 답변이 돌아온다.
5. 터미널에 fatal error 없이 로그가 이어진다.

문제가 나면 아래부터 확인한다.

```bash
python --version
ollama list
ollama ps
```

Windows에서 `python` 대신 `py`만 되는 환경이면 다음처럼 실행할 수도 있다.

```powershell
py -m venv .venv
py -m pip install -e ".[dev]"
py run_server.py
```

---

## 10. Tailscale 설치

Tailscale은 내 PC의 MAI Web UI를 다른 기기에서 접속하게 해주는 통로다. Funnel을 쓰면 Tailscale에 로그인하지 않은 일반 브라우저에서도 HTTPS URL로 접속할 수 있다.

주의: Funnel은 공개 인터넷 URL을 만든다. MAI 로그인 비밀번호를 약하게 두지 말고, 필요할 때만 켜는 것이 좋다.

### Windows

1. 공식 다운로드 페이지를 연다.
   - https://tailscale.com/download
2. Windows용 Tailscale을 설치한다.
3. 시작 메뉴에서 Tailscale을 실행한다.
4. 브라우저가 열리면 Tailscale 계정으로 로그인한다.
5. PowerShell을 새로 열고 확인한다.

```powershell
tailscale status
```

기기 목록이 보이면 연결된 것이다.

### macOS

1. https://tailscale.com/download 에서 macOS용 Tailscale을 설치한다.
2. 앱을 실행하고 로그인한다.
3. Terminal에서 확인한다.

```bash
tailscale status
```

Funnel로 포트를 공개하려면 macOS Tailscale 앱 종류와 권한 제한이 있을 수 있다. Tailscale 공식 문서 기준으로, macOS에서 Funnel 포트 공유는 지원되는 Tailscale 앱 변형이 필요하다.

### Linux

Tailscale 공식 다운로드 페이지에서 배포판에 맞는 명령을 사용한다.

Ubuntu/Debian 계열의 일반적인 흐름은 다음과 같다.

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale status
```

`tailscale up`을 실행하면 로그인 링크가 나오고, 브라우저에서 승인하면 연결된다.

---

## 11. Tailscale Funnel 설정

MAI는 `TAILSCALE_FUNNEL=true`일 때 시작 과정에서 아래 명령을 자동으로 실행한다.

```bash
tailscale funnel --bg --yes 8000
tailscale funnel status
```

즉 사용자가 직접 매번 `tailscale funnel`을 칠 필요는 없다. 다만 첫 사용 때는 Tailscale 쪽에서 Funnel 사용 승인이 필요할 수 있다.

### 11.1 먼저 수동으로 한번 확인하기

처음에는 MAI 자동 실행 전에 수동 명령으로 권한 문제를 확인하는 것이 쉽다.

```bash
tailscale funnel 8000
```

브라우저 승인 화면이 나오면 승인한다. 성공하면 이런 식의 공개 URL이 표시된다.

```text
Available on the internet:
https://your-device.your-tailnet.ts.net

|-- / proxy http://127.0.0.1:8000
```

확인 후 터미널에서 `Ctrl+C`로 끈다.

### 11.2 `.env`에서 Funnel 켜기

로컬 실행이 성공했고 Tailscale도 연결됐다면 `.env`를 수정한다.

```env
TAILSCALE_FUNNEL=true
MAI_HOST=127.0.0.1
MAI_PORT=8000
```

다시 실행한다.

```bash
python run_server.py
```

성공하면 터미널에 `MAI Tailscale Funnel (public internet):` 아래로 Funnel 상태와 URL이 출력된다. 그 URL을 휴대폰이나 다른 PC 브라우저에서 열면 MAI 로그인 화면이 나온다.

### 11.3 Funnel이 실패할 때

자주 보는 원인:

- Tailscale이 설치되어 있지 않다.
- `tailscale` 명령이 PATH에 없다.
- Tailscale 로그인이 안 되어 있다.
- Funnel이 tailnet 정책에서 허용되지 않았다.
- MagicDNS 또는 HTTPS 인증서 설정이 준비되지 않았다.
- 같은 포트에 Tailscale Serve 설정이 이미 있다.
- public DNS 반영이 아직 안 됐다.

확인 명령:

```bash
tailscale status
tailscale funnel status
```

정책 문제라면 Tailscale Admin Console에서 Funnel 권한을 켜야 한다. Tailscale 문서에 따르면 CLI로 Funnel을 enable하면 필요한 HTTPS 인증서와 policy 항목을 자동 구성하려고 하지만, 권한이 부족하면 Owner/Admin/Network admin의 승인이 필요할 수 있다.

---

## 12. `.env` 주요 설정 설명

### Ollama

```env
OLLAMA_HOST=http://127.0.0.1:11434
MAIN_MODEL=ornith-1.5:9b
VISION_MODEL=
OLLAMA_REQUEST_TIMEOUT_SECONDS=300
OLLAMA_THINK=true
```

- `OLLAMA_HOST`
  - 로컬 Ollama 기본 주소다.
  - 보통 바꾸지 않는다.
- `MAIN_MODEL`
  - 기본 채팅 모델이다.
  - `ollama list`에 보이는 이름과 같아야 한다.
- `VISION_MODEL`
  - 이미지 분석용 모델이다.
  - 비워두면 이미지 분석 도구가 꺼진다.
- `OLLAMA_REQUEST_TIMEOUT_SECONDS`
  - 모델 한 번 호출을 최대 몇 초 기다릴지 정한다.
- `OLLAMA_THINK`
  - Ollama 요청의 think 설정이다.

### 저장 위치

```env
MEMORY_DB_PATH=./data/memory.sqlite3
SENTENCE_BREAKER_DB_PATH=./data/sentence_breaker.sqlite3
CHAT_DB_PATH=./data/chat.sqlite3
MAI_UPLOAD_ROOT=./mai_uploads
```

- `MEMORY_DB_PATH`
  - 장기 기억 DB다.
- `SENTENCE_BREAKER_DB_PATH`
  - 기억 검색용 문장/개념 분리 DB다.
- `CHAT_DB_PATH`
  - Web UI 대화 기록 DB다.
- `MAI_UPLOAD_ROOT`
  - 업로드 파일이 저장되는 폴더다.

### 계정

```env
OWNER_USERS=[{"user_id":"owner","user_pw":"change-me","db_id":"local-user"}]
TRIAL_USERS=[{"user_id":"체험판","user_pw":"0000","db_id":"trial-default"}]
```

각 계정은 세 값을 가진다.

| 필드 | 뜻 | 바꿔도 되나 |
| --- | --- | --- |
| `user_id` | 로그인 ID | 가능 |
| `user_pw` | 로그인 비밀번호 | 가능 |
| `db_id` | 기억, 채팅, 업로드를 묶는 내부 ID | 보통 유지 |

`user_id`를 바꿔도 `db_id`가 같으면 기존 기억과 대화 기록을 이어서 쓴다. 반대로 `db_id`를 바꾸면 새 사용자처럼 분리된다.

모든 `user_id`는 고유해야 하고 모든 `db_id`도 고유해야 한다.

### Web UI 주소

```env
MAI_HOST=127.0.0.1
MAI_PORT=8000
SESSION_HISTORY_MESSAGES=12
TAILSCALE_FUNNEL=false
```

- `MAI_HOST=127.0.0.1`
  - 같은 PC에서만 접속하는 기본값이다.
- `MAI_PORT=8000`
  - Web UI 포트다.
  - 이미 사용 중이면 `8001` 같은 다른 값으로 바꾼다.
- `SESSION_HISTORY_MESSAGES`
  - 모델에게 넘기는 최근 대화 개수이자, Web UI가 복원하는 최근 메시지 개수다.
- `TAILSCALE_FUNNEL`
  - `true`면 Tailscale 공개 URL을 자동 구성한다.

---

## 13. Owner / Trial 권한

Owner는 전체 native tool을 사용할 수 있다.

Trial은 read/search 계열과 자기 upload directory 내부의 제한된 write만 사용할 수 있다.

```text
공통
  memory_overview / memory_recall / memory_search
  current_time / calculator
  file_list / file_search / file_read
  code_search / code_read / code_symbols
  document_read
  image_analyze          # VISION_MODEL 설정 시
  web_search / web_fetch
  market_data

owner 추가
  file_write / file_create / file_delete / file_move / file_copy
  terminal_run

trial 추가
  file_write / file_create
  └─ 자기 upload directory 내부만
```

Trial upload ownership은 `db_id` 기준이다. `user_id`를 바꿔도 같은 `db_id`를 유지하면 기존 upload directory가 이어진다.

---

## 14. 로그인과 세션

Owner/Trial 모두 ID + password로 로그인한다. 성공하면 서버가 Bearer token을 발급한다.

같은 `user_id`로 새 로그인하면 기존 token을 폐기한다. 즉 **new-login-wins** 정책이다.

브라우저는 마지막으로 성공 로그인한 `user_id`만 localStorage에 기억한다. Password는 저장하지 않는다.

PC에서 사용하다 폰으로 같은 계정에 로그인하면 PC token이 무효화될 수 있다. PC로 돌아왔을 때 password만 다시 입력하면 된다.

로그인 실패 응답은 ID가 틀렸는지 password가 틀렸는지 구분해 노출하지 않는다.

---

## 15. Persistent chat

대화 기록은 로그인 ID가 아니라 `db_id` 기준으로 `CHAT_DB_PATH`의 SQLite에 저장된다.

Web UI 전용 table:

```text
web_chat_messages
```

기존 SQLite 파일 안에 다른 용도의 `chat_messages`가 있어도 함부로 변형하지 않는다.

Startup migration 규칙:

- `web_chat_messages`가 없고 기존 `chat_messages`가 과거 persistent-chat의 정확히 알려진 schema이면 구조적으로 이관한다.
- 기존 `chat_messages`가 unrelated schema이면 그대로 보존하고 별도 `web_chat_messages`를 생성한다.
- `web_chat_messages` 자체가 알 수 없는 schema이면 startup failure로 멈춘다.

`SESSION_HISTORY_MESSAGES=12`이면 model에게 최근 12 messages를 전달하고, 새로 로그인하거나 페이지를 다시 열었을 때 화면에도 같은 최근 12 messages만 복원한다. 더 오래된 기록은 SQLite에 남아 있지만 현재 UI/model context에는 포함하지 않는다.

Browser/device가 사라져도 server process가 살아 있으면 running resumable chat job은 계속될 수 있다. 완료된 assistant answer는 persistent chat에 저장되어 다음 접속에서 보인다.

단, running job 자체는 process-memory state이므로 server process restart 이후 계산을 이어서 수행하는 durable queue는 아니다.

---

## 16. 모델 선택과 verifier

Owner:

```text
GET /models -> 설치된 Ollama models
/chat -> 선택 가능
```

Trial:

```text
GET /models -> MAIN_MODEL 하나
/chat -> MAIN_MODEL 고정
```

Trial client가 UI를 우회해 다른 model을 직접 보내도 server boundary에서 거부한다.

Memory fact extraction은 해당 chat turn에서 실제 사용한 동일 model을 `think=False`로 사용한다. 별도 `MEMORY_MODEL`은 없다.

Final answer는 사용자에게 보이기 전에 verifier를 거친다. 현재 주요 검증 축:

```text
numeric grounding
claim/evidence grounding
scope preservation
temporal consistency
action outcome verification
task alignment
evidence coverage
```

Evidence coverage는 이미 확보된 useful evidence를 model이 너무 조심스럽게 버리고 generic answer로 후퇴하는 것을 막기 위한 축이다. 추가 검색 가능성이나 exhaustive completeness 자체를 요구하지 않는다.

Coverage correction은 최대 2회다. 이후에는 coverage 부족만으로 final을 더 붙잡지 않는다.

---

## 17. 파일 업로드

기본 upload root:

```text
./mai_uploads/
```

필요하면 `.env`에서 바꾼다.

```env
MAI_UPLOAD_ROOT=./mai_uploads
```

Owner는 upload root를 사용하고 Trial은 `db_id`에서 파생된 전용 directory를 사용한다.

같은 account directory에서 같은 filename을 조용히 overwrite하지 않고 HTTP 409로 실패한다. Filename에 path separator를 허용하지 않는다.

업로드된 파일의 실제 path가 input에 추가되므로 model이 `file_read`, `document_read`, `image_analyze` 등을 사용할 수 있다.

---

## 18. Trial 초기화

`.env.example`의 기본 체험 계정:

```text
ID: 체험판
PW: 0000
```

다른 사용자에게 재사용하기 전 server를 종료하고 dry-run을 먼저 확인한다.

```bash
python reset_trial.py 체험판 --dry-run
python reset_trial.py 체험판
```

Reset은 `user_id`로 account를 찾고 persistent ownership은 `db_id` 기준으로 제거한다.

대상:

- 해당 Trial memory
- `web_chat_messages`의 해당 `db_id` conversation
- 해당 Trial upload directory

Unrelated legacy chat table은 임의 삭제하지 않는다.

---

## 19. 자주 나는 문제

### `python` 명령을 찾을 수 없음

Windows라면 Python 설치 때 **Add python.exe to PATH**를 체크했는지 확인한다. PowerShell을 새로 열어도 안 되면 `py --version`도 시도한다.

```powershell
py --version
```

### `pip install` 중 Git 관련 오류

이 프로젝트 dependency 중 GitHub에서 받는 패키지가 있다. Git이 설치되어 있고 `git --version`이 동작해야 한다.

### Ollama 연결 오류

Ollama 앱 또는 서비스가 켜져 있는지 확인한다.

```bash
ollama list
ollama ps
```

`OLLAMA_HOST`를 바꾼 적이 없다면 기본값은 다음이다.

```env
OLLAMA_HOST=http://127.0.0.1:11434
```

### 모델을 찾을 수 없음

`.env`의 `MAIN_MODEL` 이름과 `ollama list` 결과가 정확히 같은지 확인한다.

### 포트 8000을 이미 사용 중

`.env`에서 포트를 바꾼다.

```env
MAI_PORT=8001
```

그 다음:

```bash
python run_server.py
```

브라우저 주소도 바뀐다.

```text
http://127.0.0.1:8001
```

### Tailscale Funnel URL이 바로 안 열림

Tailscale 문서 기준으로 Funnel public DNS 반영에는 시간이 걸릴 수 있다. 몇 분 기다린 뒤 다시 시도한다.

확인:

```bash
tailscale funnel status
```

### `TAILSCALE_SERVE` 오류

`TAILSCALE_SERVE=true`는 더 이상 쓰지 않는다. `.env`에서 삭제하거나 false로 바꾸고, 공개 URL이 필요하면 다음만 사용한다.

```env
TAILSCALE_FUNNEL=true
```

---

## 20. 업데이트

이미 저장소를 받아둔 상태에서 최신 코드를 가져오려면:

```bash
git pull
```

가상환경을 켠 뒤 dependency를 다시 맞춘다.

```bash
python -m pip install -e ".[dev]"
```

그 다음 다시 실행한다.

```bash
python run_server.py
```

---

## 21. 테스트

개발자가 변경 후 확인할 때는 전체 regression test를 실행한다.

```bash
python -m pytest -q
```

Runtime, auth, persistent chat, tool selection, verifier, memory, upload 변경 후에는 전체 suite를 기준으로 확인한다.
