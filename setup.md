# MAI 첫 설치 안내서 — 비개발자용 (Windows)

이 문서는 **컴퓨터에 개발 도구가 하나도 설치되어 있지 않은 Windows 10/11 사용자**가 MAI를 처음 설치하는 방법이다. 어떤 명령을 **어디에 입력해야 하는지**, 무엇을 확인해야 하는지, 실패하면 무엇부터 검사하는지까지 순서대로 설명한다. 개발 경험이나 Git/Python 지식을 전제로 하지 않는다.

> **가장 먼저 알아둘 점**: `python -m pip install -e .`는 MAI의 Python 라이브러리를 설치하지만, Playwright가 실행할 **Chromium 브라우저 파일은 설치하지 않는다.** 반드시 별도로 `python -m playwright install chromium`을 한 번 실행해야 한다.
>
> **이 안내서는 Windows PowerShell 기준이다.** macOS/Linux 명령은 Windows 명령과 다르므로 그대로 복사하면 안 된다. 첫 설치는 **로컬 PC에서 성공시킨 뒤** Tailscale을 연결한다.
>
> **비밀번호 주의**: `.env`는 로그인 비밀번호를 평문으로 포함한다. GitHub, 메신저, 스크린샷에 공개하지 않는다. 특히 Tailscale **Funnel은 로그인하지 않은 외부 인터넷에서도 접속 가능한 공개 주소**를 만든다.

## 전체 순서

1. 준비 사항 및 Windows 확인
2. Git 설치 (GitHub에서 프로젝트를 내려받는 도구)
3. Python 설치
4. Ollama 설치 및 사용할 모델 내려받기
5. MAI 프로젝트 다운로드
6. PowerShell에서 프로젝트 폴더 열기
7. 프로젝트 전용 Python 가상환경 생성
8. Python 패키지 설치
9. Playwright Chromium 설치
10. 설정 파일 `.env` 만들고 비밀번호/모델 설정
11. 로컬 서버 최초 실행
12. 브라우저 로그인 및 기능별 동작 확인
13. Tailscale 설치 및 로그인
14. Tailscale Funnel 설정과 외부 접속
15. 다음 날 재실행/업데이트/종료 방법
16. 자주 발생하는 오류 해결
17. 선택 사항: SSH 원격 PC 관리
18. 최종 체크리스트

---

## 0. 시작 전에 준비할 것

- Windows 10/11 PC (관리자 권한으로 프로그램을 설치할 수 있어야 한다).
- 인터넷 연결. Git, Python, Ollama, 모델, Python 패키지, Chromium 설치에 사용한다.
- PC 저장 공간. 설치 도구 외에 **모델과 Chromium을 위한 추가 여유 공간**이 필요하다. 모델별 크기는 다르므로 설치 화면과 `ollama list`에서 실제 크기를 확인한다.
- 모델 실행을 위한 충분한 메모리. 필요한 RAM/VRAM은 모델 및 실행 환경에 따라 달라진다. 부족하면 더 작은 모델을 선택한다.
- 외부 접속까지 하려면 Tailscale에 로그인할 계정.
- 아래에서 명령을 입력하는 창은 별도 표기가 없으면 **Windows PowerShell**이다. PowerShell은 시작 메뉴 검색창에 `PowerShell`을 입력해 실행한다. 일반적인 단계에는 관리자 실행이 필요하지 않다.

### 0-1. Windows 버전과 저장 공간 확인

1. 시작 메뉴 → **설정** → **시스템** → **정보**를 연다.
2. Windows 버전과 시스템 종류(일반적으로 64비트)를 확인한다.
3. 파일 탐색기(`Win + E`) → **내 PC**에서 C: 드라이브의 남은 공간을 확인한다.
4. 이 가이드에서는 작업 폴더로 `$HOME\Documents\Git\MAI_MyAI_sllm`을 사용한다. 다른 드라이브를 써도 되지만, 이후 명령에서 경로를 동일하게 바꿔야 한다.

### 0-2. 명령어 복사 방법

- 코드 상자의 내용만 복사해 **PowerShell** 창에 붙여넣고 Enter를 누른다.
- `PS C:\...`나 `(.venv)` 등 **명령 프롬프트 앞부분은 입력하지 않는다**. 예시 화면에 등장하면 위치/상태 표시일 뿐이다.
- 설치가 실패하면 다음 단계로 넘어가지 않는다. 오류 메시지를 저장하거나 복사해 원인을 먼저 해결한다.

---

## 1. Git 설치

**Git**은 GitHub 저장소를 컴퓨터로 복사하고 새 버전을 받아오는 프로그램이다. GitHub 웹사이트 자체를 설치하는 것은 아니다.

1. 웹브라우저에서 공식 사이트 **https://git-scm.com/downloads/win** 을 연다.
2. Windows 설치 파일을 내려받는다.
3. 다운로드한 설치 파일을 실행한다.
4. 설치 마법사에서 특별한 설정이 필요 없다면 기본 옵션으로 진행한다. Git을 명령줄에서 쓸 수 있게 하는 PATH 옵션은 기본 설정을 유지한다.
5. 설치가 끝나면 열려 있던 PowerShell을 모두 닫고 **새 PowerShell 창**을 연다.
6. 아래 명령으로 설치를 확인한다.

```powershell
git --version
```

`git version ...`이 출력되면 성공이다. `git을 찾을 수 없음`이 나오면 터미널을 새로 열거나 설치 과정의 PATH 설정을 확인한다.

> GitHub Desktop(https://desktop.github.com/)은 선택 사항이다. 이 안내서는 Git만으로 설치하므로 GitHub Desktop이 없어도 된다. GitHub Desktop을 원하면 별도 설치해도 되지만 아래 명령을 그대로 따라가는 것이 가장 일관적이다.

---

## 2. Python 설치

MAI는 **Python 3.11 이상**이 필요하다. 처음 설치할 때는 프로젝트 호환성을 우선하여 **Python 3.11의 최신 유지보수 버전(64비트)**을 권장한다.

1. 공식 Python 사이트 **https://www.python.org/downloads/windows/** 로 이동한다.
2. Python 3.11 항목의 Windows 64-bit installer가 제공되는 버전을 선택한다. 설치 가능한 패키지와 Python 버전은 공식 다운로드 화면에서 확인한다.
3. 설치 파일 실행 후 **Add python.exe to PATH**라는 체크박스가 나오면 체크한다. 설치 프로그램 UI가 다를 수 있으므로 표시되지 않는다면 설치 후 `py` 명령도 확인한다.
4. **Install Now**로 설치하고 완료 화면을 닫는다.
5. PowerShell을 새로 연 다음 확인한다.

```powershell
python --version
py --version
python -m pip --version
```

- `python --version`에 `Python 3.11.x` 이상이 보이면 성공.
- `python`이 Microsoft Store로 이동하거나 인식되지 않으면 시작 메뉴에서 **앱 실행 별칭 관리(Manage app execution aliases)**를 찾아 Windows의 `python.exe` / `python3.exe` 별칭을 확인한다. 또는 Python 설치 관리자의 PATH 옵션을 수정한다.
- `py` 명령만 동작할 경우, **가상환경 생성 단계에서만** `py -3.11 -m venv .venv`를 사용해도 된다. 가상환경 활성화 이후에는 `python`을 사용한다.
- `pip`을 직접 실행하기보다 `python -m pip`을 사용하면 현재 선택한 Python에 설치한다는 점을 확인할 수 있다.

Python 공식 Windows 문서: https://docs.python.org/3.11/using/windows.html

---

## 3. Ollama 설치 및 모델 준비

Ollama는 로컬에서 AI 모델을 다운로드하고 실행하는 프로그램이다. MAI는 Ollama에 접속해 답변을 생성한다.

### 3-1. Ollama 설치

1. **https://ollama.com/download** 접속.
2. Windows용 Ollama 설치 프로그램 다운로드.
3. 파일을 실행해 설치를 완료한다.
4. 시작 메뉴에서 Ollama를 실행한다. 필요한 경우 시스템 트레이(화면 오른쪽 아래)에서 Ollama가 실행 중인지 확인한다.
5. 새 PowerShell을 열어 설치 상태를 확인한다.

```powershell
ollama --version
ollama list
```

`ollama list`의 목록이 비어 있는 것은 처음이라면 정상이다.

### 3-2. 사용할 모델 설치

이 저장소의 `.env.example` 기본 모델 이름은 `ornith-1.5:9b`다. **모델을 다운로드할 때는 해당 이름이 Ollama 라이브러리에서 실제로 제공되는지 확인한다.** 프로젝트 문서의 예시 이름이라고 해서 모든 PC에서 자동으로 설치되는 것은 아니다.

```powershell
ollama pull ornith-1.5:9b
```

명령이 성공하면 확인한다.

```powershell
ollama list
ollama run ornith-1.5:9b
```

질문을 하나 입력해 답변이 오면 모델이 동작한다. 채팅을 종료할 때는 `/bye`를 입력한다.

**중요:** `ollama pull`이 `model not found` 등으로 실패했다면 성공한 척하고 넘어가지 않는다. Ollama 공식 라이브러리(https://ollama.com/library)에서 **실제로 내려받을 수 있는 모델**을 선택하고, 이후 `.env`의 `MAIN_MODEL=`에 그 이름을 **정확히 동일하게** 적는다. 모델이 설치되지 않았거나 이름이 다르면 MAI 답변 요청이 실패한다.

### 3-3. 이미지 분석(선택)

MAI가 사진이나 스크린샷을 별도 비전 모델로 분석하도록 하려면, `.env`의 `VISION_MODEL`과 **실제로 설치한 비전 모델 이름**을 일치시킨다.

```powershell
ollama pull gemma4:12b
```

이 명령이 실패하거나 PC에 부담이 되면 이미지 분석은 건너뛰고 나중에 `VISION_MODEL=`을 **빈 값**으로 설정해도 된다. 빈 값일 때는 MAI의 `image_analyze` 도구가 등록되지 않는다.

---

## 4. GitHub에서 MAI 프로젝트 다운로드

프로젝트 주소: https://github.com/FoggyCaligo/MAI_MyAI_sllm

이 프로젝트의 개발 검증용 작업 브랜치는 **dev**다. 이 안내서는 사용자가 수동으로 검증하는 개발 환경을 전제로 **dev**를 받는다. main을 원한다면 아래 `git switch dev` 대신 `git switch main`을 입력한다. 아직 dev에 병합되지 않은 PR 변경은 dev에 존재하지 않는다는 점에 주의한다.

1. PowerShell을 연다.
2. 다음 명령으로 `문서\Git` 폴더를 만든다.

```powershell
New-Item -ItemType Directory -Force -Path "$HOME\Documents\Git"
Set-Location "$HOME\Documents\Git"
```

3. GitHub 저장소를 컴퓨터에 복사한다.

```powershell
git clone https://github.com/FoggyCaligo/MAI_MyAI_sllm.git
```

4. 방금 받은 프로젝트 폴더로 이동한다.

```powershell
Set-Location .\MAI_MyAI_sllm
```

5. dev 브랜치를 선택하고 상태를 확인한다.

```powershell
git fetch origin
git switch dev
git status
```

`On branch dev`, `working tree clean` 등이 출력되면 성공이다. **GitHub에서 ZIP 다운로드도 가능하지만**, 이후 `git pull`로 업데이트하려면 Git clone 방식이 편하므로 이 문서는 clone을 기본으로 한다.

### 4-1. 다운로드한 파일 확인

파일 탐색기로 `Documents\Git\MAI_MyAI_sllm`을 연다. 안에 다음 항목들이 보여야 한다.

- `run_server.py`: MAI 서버 시작 파일
- `pyproject.toml`: 필요한 Python 패키지 목록
- `.env.example`: 처음 설정할 때 복사할 예시 파일(숨김 파일 표시가 필요할 수 있음)
- `mai`: 프로그램 본체 폴더
- `data`: 데이터베이스 관련 폴더
- `README.md`, `RUN_UI.md`: 추가 설명

### 4-2. 앞으로 PowerShell에서 프로젝트 폴더를 여는 방법

PowerShell을 다시 열었을 때는 아래 명령으로 항상 해당 폴더로 이동한다.

```powershell
Set-Location "$HOME\Documents\Git\MAI_MyAI_sllm"
```

이후 명령을 다른 폴더에서 실행하면 `pyproject.toml`이나 `run_server.py`를 찾지 못할 수 있다.

---

## 5. 가상환경 만들기

**가상환경(`.venv`)**은 MAI 전용 Python 패키지 공간이다. 다른 프로그램과 패키지가 충돌하는 문제를 줄여준다. **처음 한 번 생성**, 그다음부터는 사용할 때 **활성화만** 하면 된다.

1. PowerShell에서 프로젝트 폴더인지 확인한다.

```powershell
Get-Location
Test-Path .\pyproject.toml
```

`Test-Path`가 `True`여야 한다.

2. 가상환경 생성:

```powershell
python -m venv .venv
```

이 명령이 Python 버전 문제로 실패하고 `py`가 동작한다면:

```powershell
py -3.11 -m venv .venv
```

3. 가상환경 활성화:

```powershell
.\.venv\Scripts\Activate.ps1
```

4. 실행 정책 때문에 거부되는 경우, **현재 PowerShell 창에만** 허용하고 다시 시도한다.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

PowerShell 앞에 `(.venv)`가 보이면 활성화됐다. `python -c "import sys; print(sys.executable)"`을 입력했을 때 프로젝트 내부 `.venv\Scripts\python.exe`가 출력되는지도 확인한다.

> 이후 문서의 `python`은 별도 설명이 없으면 **활성화한 가상환경의 Python**이다. 새로운 PowerShell을 열 때마다 이 활성화 단계가 필요하다.

---

## 6. Python 패키지 설치

가상환경이 켜져 있는지 확인한 상태에서, 프로젝트 폴더에서 **순서대로** 실행한다.

```powershell
python -m pip install --upgrade pip
python -m pip install -e .
```

- `pip`: Python 패키지 설치 도구.
- `-e .`: 현재 폴더의 MAI를 **편집 가능한(editable) 방식**으로 설치. 코드가 바뀌어도 패키지를 매번 복사할 필요가 없다.
- 설치 중 GitHub에서 `sentence-breaker`를 다운로드하므로 인터넷과 Git이 필요하다.
- 설치 중 오류가 나면 완료된 것으로 간주하면 안 된다. 마지막 오류 메시지를 확인한다.

테스트용 `pytest`까지 설치하고 싶으면 기본 설치 대신 아래 명령을 사용해도 된다.

```powershell
python -m pip install -e ".[dev]"
```

정상 설치 여부 확인:

```powershell
python -m pip show mai-myai-sllm
python -m pip show playwright
```

둘 다 패키지 정보가 출력되면 Python 패키지 설치가 완료됐다. 그러나 **Chromium 본체는 아직 없다.** 다음 단계가 필요하다.

---

## 7. Playwright Chromium 브라우저 설치 (중요)

MAI의 `browser_open` 등은 실제 브라우저에서 JavaScript를 실행한다. `pip install`은 브라우저 제어용 Python 라이브러리만 설치한다. **Chromium 바이너리는 별도로 설치해야 한다.**

가상환경을 활성화한 상태에서:

```powershell
python -m playwright install chromium
```

다운로드가 완료될 때까지 두고, 오류 없이 끝나는지 확인한다. 이는 **PC/브라우저 버전별 최초 설치에 필요하며**, Playwright 업데이트로 요구하는 브라우저 버전이 변경되면 다시 설치할 수 있다.

실제 실행 테스트:

```powershell
python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); print('Chromium 실행 성공'); b.close(); p.stop()"
```

`Chromium 실행 성공`이 출력되면 준비 완료다. Chromium 설치에 실패하면 `browser_open`도 실패한다. `web_fetch`가 잘 동작하더라도 이는 별개다.

참고: https://playwright.dev/python/docs/browsers

---

## 8. `.env` 환경 설정 파일 만들기

`.env.example`은 예시 파일이므로 그대로 쓰지 않는다. 실제 실행 설정은 **프로젝트 루트의 `.env`**에 둔다.

1. 프로젝트 폴더에서 PowerShell 명령을 실행한다.

```powershell
Copy-Item .env.example .env
notepad .env
```

2. 메모장이 열리면 아래 항목을 찾아 수정한다. **모든 항목을 새로 붙여넣거나, 기존에 없는 설정을 추측해 만들 필요는 없다.** 나머지 옵션은 우선 예시 파일 값을 유지한다.

```dotenv
OLLAMA_HOST=http://127.0.0.1:11434
MAIN_MODEL=ornith-1.5:9b
VISION_MODEL=
MAI_HOST=127.0.0.1
MAI_PORT=8000
TAILSCALE_FUNNEL=false
```

- `MAIN_MODEL`: `ollama list`에 있는 실제 설치 모델 이름을 입력한다. `ornith-1.5:9b`가 설치되지 않았다면 성공적으로 설치한 다른 모델 이름을 넣어야 한다.
- `VISION_MODEL`: 비전 모델을 다운로드하지 않았다면 빈 값으로 둔다. 설치했다면 이름을 입력한다.
- `TAILSCALE_FUNNEL=false`: **최초 로컬 테스트에서는 반드시 false를 권장**. 네트워크 공개는 뒤에서 별도로 켠다.
- `MAI_HOST=127.0.0.1`: 같은 PC에서만 접속하는 로컬 주소. 외부 접속을 위해 `0.0.0.0`으로 변경할 필요는 없다.
- `MAI_PORT=8000`: MAI 웹서버 포트.

3. `.env`의 계정 부분을 찾아 반드시 수정한다. 다음은 **형식 설명용**이며 표시된 암호를 그대로 사용하지 말 것.

```dotenv
OWNER_USERS=[{"user_id":"owner","user_pw":"여기에_본인만_아는_긴_비밀번호","db_id":"local-user"}]
TRIAL_USERS=[{"user_id":"체험판","user_pw":"여기도_반드시_변경","db_id":"trial-default"}]
```

- `user_id`: 로그인 ID.
- `user_pw`: 로그인 비밀번호. 강력하고 서로 다른 비밀번호를 설정한다.
- `db_id`: 채팅/메모리 데이터의 내부 식별자. 이미 사용 중인 설치에서는 임의 변경하지 않는다.
- 계정 줄은 **유효한 JSON 배열** 형식을 유지한다. 쌍따옴표, 대괄호, 중괄호, 쉼표를 임의로 삭제하지 않는다.
- 쓰지 않는 체험 계정의 약한 기본 비밀번호를 그대로 방치하지 않는다. 체험 계정을 운영할 계획이 없다면 해당 계정 설정을 관리할 방법을 프로젝트 정책에 맞게 검토한다.

4. **파일 → 저장**을 누르고 메모장을 닫는다.
5. 다음 명령으로 파일 존재만 확인한다. **비밀번호가 담긴 전체 내용을 터미널에 출력해 공유하지 않는다.**

```powershell
Test-Path .\.env
```

`True`면 파일이 존재한다.

### 8-1. 데이터 파일을 이해하기

예시 설정에는 다음 경로들이 있다.

```dotenv
MEMORY_DB_PATH=./data/memory.sqlite3
SENTENCE_BREAKER_DB_PATH=./data/sentence_breaker.sqlite3
CHAT_DB_PATH=./data/chat.sqlite3
LOG_DB_PATH=./data/log.sqlite3
MAI_UPLOAD_ROOT=./mai_uploads
```

- `memory.sqlite3`: MAI의 장기기억.
- `chat.sqlite3`: Web UI 대화 저장.
- `log.sqlite3`: 오류 조사와 감사용 실행/툴 로그. 민감한 정보가 포함될 수 있다.
- `sentence_breaker.sqlite3`: 문장/개념 처리 관련 데이터.
- `mai_uploads`: 사용자 업로드 파일 디렉터리.

**이미 운영 중인 PC라면 이 파일들을 지우거나 덮어쓰지 않는다.** 재설치·업데이트 시 백업이 필요하다. 이 저장소에는 버전 관리된 데이터 파일이 들어 있을 수 있으므로 특히 `git reset --hard` 또는 무분별한 폴더 삭제를 피한다.

---

## 9. MAI 로컬 서버 처음 실행

필수 조건 확인:

```powershell
git --version
python --version
ollama list
Test-Path .\.env
```

이제 프로젝트 폴더에서, 가상환경을 활성화한 상태로 실행한다.

```powershell
python run_server.py
```

성공했다면 터미널에 다음과 **비슷한** 문구가 표시된다.

```text
MAI local: http://127.0.0.1:8000
MAI Tailscale Funnel: disabled (set TAILSCALE_FUNNEL=true to enable)
Uvicorn running on http://127.0.0.1:8000
```

서버 실행 중에는 **PowerShell 창을 닫지 않는다.** 이후 검사용 명령은 새 PowerShell 창에서 실행한다.

### 9-1. 브라우저로 접속

1. Chrome/Edge 브라우저를 연다.
2. 주소창에 `http://127.0.0.1:8000`을 입력한다.
3. 로그인 페이지가 나오는지 확인한다.
4. `.env`에서 설정한 `OWNER_USERS`의 `user_id`와 `user_pw`를 입력한다.
5. 짧은 대화(예: “안녕, 짧게 답해줘”)를 보낸다.
6. 정상 답변이 오는지 확인한다. 초기에 모델 다운로드/로딩 및 PC 성능에 따라 시간이 걸릴 수 있다.

### 9-2. 상태 주소 확인

다른 PowerShell 또는 브라우저에서 아래 주소에 접속할 수 있다.

```text
http://127.0.0.1:8000/health
```

`status: ok`에 해당하는 응답이 표시되면 웹서버 런타임 상태 확인이 된 것이다. **이것만으로 Ollama 응답이나 모든 도구의 정상 동작이 증명되지는 않는다.** 실제 채팅 테스트도 수행한다.

### 9-3. JavaScript 브라우저 탐색 테스트

브라우저 툴이 포함된 커밋이 실제 설치 브랜치에 있어야 한다. MAI에게 공개 웹페이지 하나를 열고 JavaScript 실행 후 렌더링된 목록과 실제 링크를 읽으라고 요청한다. 예를 들어:

> `https://comicw.net/g/all.php?fare=338&tag=스텔라이브` 를 `browser_open`으로 열고 렌더링된 목록의 제목과 실제 href를 `browser_snapshot`에서 확인해줘. 목록이 안 보이면 확인하지 못했다고 답해줘.

- 툴 로그에 `browser_open`, 필요 시 `browser_snapshot` 호출이 나타나는지 확인한다.
- 표시된 링크와 실제 페이지가 일치하는지 직접 비교한다.
- 브라우저 오픈/탐색이 실패했는데 성공했다고 답하면 잘못된 상태다.
- 특정 페이지가 봇 접속 차단/인증/동적 로딩 문제로 실패할 수 있다. **브라우저 설치가 성공했다고 모든 사이트의 글을 읽을 수 있는 것은 아니다.**

---

## 10. Tailscale 설치 — 로컬 실행을 확인한 뒤

**원격 접속이 필요 없다면 10~12단계는 생략해도 된다.**

용어:
- **Tailscale**: 여러 기기를 안전하게 같은 사설 네트워크에 연결할 수 있는 프로그램.
- **Tailscale Serve**: tailnet 내부 기기만 접근하도록 로컬 서비스를 공유하는 기능.
- **Tailscale Funnel**: **공개 인터넷에서도** 접속할 수 있는 HTTPS 주소로 로컬 서비스를 공개하는 기능. MAI 현재 설정은 Funnel을 사용한다.

### 10-1. 설치 및 로그인

1. 공식 문서 https://tailscale.com/docs/install/windows 또는 다운로드 https://tailscale.com/download 로 이동한다.
2. Windows용 설치 프로그램(.exe)을 다운로드한다.
3. 설치 프로그램을 실행하고 안내에 따라 설치한다.
4. 작업 표시줄 오른쪽 아래 **시스템 트레이**에서 Tailscale 아이콘을 찾는다. 안 보이면 `^`(숨겨진 아이콘 보기)를 누른다.
5. Tailscale 아이콘을 클릭 또는 우클릭한 뒤 **Log in**을 선택한다.
6. 열린 브라우저에서 사용 중인 인증 계정으로 로그인하고 기기 연결 요청을 승인한다.
7. 새 PowerShell 창을 연다.

```powershell
tailscale version
tailscale status
tailscale ip -4
```

- 상태에 연결된 PC 정보가 표시되면 Tailscale 연결을 확인할 수 있다.
- `tailscale` 명령이 없으면 Tailscale이 설치됐는지 확인하고 **PowerShell을 새로 열어** 다시 시도한다.
- 출력된 IP/기기 이름이 있다고 해서 아직 Funnel이 공개된 것은 아니다.

### 10-2. 공개 접속 전 보안 확인

Funnel은 인터넷의 **누구든지 MAI 로그인 페이지에 연결을 시도할 수 있는 주소**다. `.env`의 기본 암호를 반드시 변경한다. MAI가 로컬 PC의 파일/터미널 툴을 제공할 수 있으므로 소유자 계정을 강한 비밀번호로 보호한다. 원격 접속에 꼭 공개 URL이 필요하지 않다면 **Funnel을 활성화하지 않는다.**

Tailscale Funnel에는 MagicDNS/HTTPS/ACL(정책) 등 필요한 조건이 있다. 초기 사용 시 관리자 콘솔에서 Funnel 사용을 승인해야 할 수 있다. 공식 설명: https://tailscale.com/docs/features/tailscale-funnel

---

## 11. Tailscale Funnel 첫 설정 및 외부 접속

**MAI는 현재 `TAILSCALE_FUNNEL=true`로 실행하면 내부에서 다음 명령을 실행한다.**

```powershell
tailscale funnel --bg --yes 8000
tailscale funnel status
```

### 11-1. 사전 권한 확인

1. MAI 서버는 우선 `TAILSCALE_FUNNEL=false`인 상태에서 **로컬로 실행 중**이어야 한다.
2. **새 PowerShell 창**을 연다.
3. 다음 명령으로 Funnel 설정을 시도한다.

```powershell
tailscale funnel --bg 8000
```

Tailscale이 브라우저를 열어 허용을 요청한다면 해당 단계를 완료한다. 관리자 정책에 따라 추가 승인이 필요할 수 있다. 성공하면 상태를 확인한다.

```powershell
tailscale funnel status
```

**처음부터 실패했다면 MAI의 문제로 단정하지 않는다.** Tailscale 앱 로그인, Funnel 허용 정책, 포트 및 CLI 오류를 확인한다.

> 참고: `--bg`는 Funnel 설정을 **백그라운드로 유지**한다. **MAI 서버를 종료해도 공개 Funnel 설정은 자동 삭제되지 않는다.** 서버가 꺼져 있을 때는 대상에 연결되지 않을 수 있지만, 서버를 다시 켜면 기존 공개 설정이 동작할 수 있다. 따라서 보안상 필요 없을 때는 명시적으로 비활성화해야 한다.

### 11-2. MAI 자동 Funnel 활성화

1. MAI 실행 중인 PowerShell 창에서 `Ctrl + C`를 눌러 서버를 종료한다.
2. 가상환경이 활성화된 프로젝트 폴더 PowerShell에서 `.env`를 연다.

```powershell
notepad .env
```

3. 아래 항목을 변경한 후 저장한다.

```dotenv
TAILSCALE_FUNNEL=true
MAI_HOST=127.0.0.1
MAI_PORT=8000
```

4. 다시 서버를 실행한다.

```powershell
python run_server.py
```

5. 출력에서 `MAI Tailscale Funnel (public internet):`와 `https://...ts.net` 형태의 URL을 확인한다.
6. 휴대폰 브라우저에서 해당 **실제 출력 URL**을 열어본다. URL은 각 Tailscale 계정/기기마다 다르므로 예제 URL을 복사하지 않는다.
7. MAI 로그인 화면이 표시되는지 확인한다.

문제가 없다면 외부 접속 준비가 완료된다. 휴대폰에 Tailscale 앱을 설치할 필요가 없는 것이 Funnel의 특징이지만, 그만큼 공개 주소라는 점을 잊지 않는다.

### 11-3. 공개 Funnel 끄기

공개 주소를 더 사용하지 않을 때는 `.env`를 `TAILSCALE_FUNNEL=false`로 바꾸는 것만으로 **이미 설정된 Funnel이 취소되지는 않는다.**

Tailscale 공식 CLI의 설정 제거 명령으로 **현재 Funnel 설정을 확인한 다음** 종료한다. 이 프로젝트가 기본 HTTPS Funnel 포트를 사용했다면 일반적으로:

```powershell
tailscale funnel status
tailscale funnel --https=443 off
tailscale funnel status
```

다른 Funnel 설정까지 전부 지워도 되는 경우에만 공식 문서를 확인하고 `tailscale funnel reset`을 고려한다. `reset`은 해당 기기의 Funnel 설정 전체에 영향을 줄 수 있다. 자세한 내용: https://tailscale.com/docs/reference/tailscale-cli/funnel

---

## 12. 이후 매번 MAI 실행하는 방법

**Windows를 재시작하거나 PowerShell 창을 새로 연 다음**에는 아래 순서로 실행한다.

1. Ollama가 실행 중인지 확인한다(시작 메뉴 또는 작업 표시줄).
2. PowerShell을 연다.
3. 다음 세 줄을 차례로 입력한다.

```powershell
Set-Location "$HOME\Documents\Git\MAI_MyAI_sllm"
.\.venv\Scripts\Activate.ps1
python run_server.py
```

PowerShell 실행 정책 오류가 있으면 5장 내용을 다시 참고한다.

4. 같은 PC 브라우저에서 `http://127.0.0.1:8000`을 열어 사용한다.
5. 종료하려면 실행 창으로 이동해 `Ctrl+C`를 누른다.
6. `deactivate`를 입력하면 가상환경 표시가 사라진다(선택 사항).

**매번 새로 설치할 필요는 없다.** `git clone`, `python -m venv`, `pip install`, `playwright install chromium`, `ollama pull`은 최초 설치나 버전 변경 때 사용하는 명령이다.

---

## 13. MAI를 새 버전으로 업데이트하는 방법

1. MAI 서버를 `Ctrl+C`로 종료한다.
2. PowerShell에서 프로젝트 폴더로 이동한다.
3. 수정한 파일이나 기록을 확인한다.

```powershell
Set-Location "$HOME\Documents\Git\MAI_MyAI_sllm"
git status
```

4. **직접 수정한 파일이 있으면** 무작정 `git pull`하지 않는다. 변경 내역을 백업하고 병합 방법을 결정한다. 특히 `data/*.sqlite3`와 `.env`를 백업한다.
5. 변경 사항이 없을 때 dev를 갱신한다.

```powershell
git switch dev
git pull --ff-only origin dev
```

6. 가상환경을 활성화하고 의존성을 최신 프로젝트 기준으로 다시 확인한다.

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m playwright install chromium
```

7. 새 버전에서 `.env.example`에 신규 설정이 생겼는지 비교하되, 기존 `.env`를 **덮어쓰지 않는다**.
8. 다시 `python run_server.py` 실행 후 로그인과 채팅을 테스트한다.

> Git 변경 사항을 강제로 초기화하는 `git reset --hard`, `git clean -fd`는 이 가이드에서 **사용하지 않는다.** 로컬 기록·데이터를 손상시킬 수 있다.

---

## 14. 자주 발생하는 오류

| 증상 / 메시지 | 확인할 것 / 조치 |
|---|---|
| `git` 명령을 찾을 수 없음 | Git 설치 확인 → PowerShell을 새로 열기 → `git --version` |
| `python` 명령이 없음 / Store 실행 | Python PATH 또는 Windows 앱 실행 별칭 확인. `py --version`도 확인 |
| `pyproject.toml` 없음 | 현재 프로젝트 폴더가 맞는지 `Get-Location` / `Test-Path .\pyproject.toml` |
| `.venv\Scripts\Activate.ps1` 실행 거부 | 현재 창에서만 `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` 실행 후 재시도 |
| `pip install -e .` 실패 | 인터넷/Git 확인, Python 3.11 이상 여부, 오류 메시지 확인. 무작정 무시하지 말 것 |
| `No module named playwright` | 가상환경 활성화 후 `python -m pip install -e .` |
| `Executable doesn't exist` / Chromium 설치 안내 | `python -m playwright install chromium` 실행 |
| `ollama` 인식 실패 | 설치 및 실행 확인 후 PowerShell 재시작 |
| `model not found` | `ollama list`와 `.env`의 `MAIN_MODEL` 정확히 일치시키기 |
| Ollama 채팅 시간이 너무 오래 걸림 | 모델 크기/PC 자원/실제 오류 확인. 타임아웃 숫자만 무작정 늘리지 않기 |
| `127.0.0.1:8000` 접속 안 됨 | 서버 실행 창 오류 확인, `.env`의 `MAI_PORT` 확인, 실행 창을 닫지 않았는지 검사 |
| `Address already in use` | 포트 8000을 사용하는 다른 프로세스 확인. 다른 MAI 서버가 이미 실행 중인지 확인 |
| MAI 로그인 실패 | `.env` 계정 JSON과 입력한 ID/비밀번호 확인. 공유된 기본 암호 사용 금지 |
| Tailscale 설치했지만 `tailscale` 명령 없음 | Tailscale 설치/PATH 확인 후 PowerShell 재시작 |
| Tailscale Funnel 실패 | `tailscale status`, `tailscale funnel status`, Funnel 권한 및 CLI 오류 확인 |
| 외부 URL은 뜨는데 접속 실패 | 서버 실행 중인지, 실제 `tailscale funnel status`의 대상 포트와 MAI 포트가 일치하는지 확인 |
| 특정 웹사이트만 `browser_open` 실패 | 사이트 차단·인증·네트워크 요청 실패 여부 확인. 정적 `web_fetch`가 성공해도 브라우저 성공을 뜻하지 않음 |

### 진단 명령 모음

**문제 상황을 확인할 때만** 아래 명령을 실행한다(서버가 실행 중인 창 대신 새 창 사용).

```powershell
git status
git branch --show-current
python --version
python -m pip show mai-myai-sllm
python -m pip show playwright
ollama list
tailscale status
tailscale funnel status
```

Tailscale을 아직 설치하지 않았다면 마지막 두 명령이 실패하는 것은 당연하다. **진단 내용을 공유할 때는 인증 토큰, 계정 정보, `.env` 내용, 사적인 URL/파일 경로를 필요한 범위 외로 노출하지 않는다.**

---

## 15. 선택 사항: Tailscale을 이용한 SSH 원격 관리

**일반적인 MAI Web UI 사용에는 SSH 설정이 전혀 필요하지 않다.** SSH는 휴대폰/다른 PC의 터미널에서 Windows PC 자체를 관리하고 싶을 때 사용하는 별도 기능이다. 공개 Funnel URL과 SSH는 서로 다른 기능이다.

프로젝트에 `scripts/setup_tailscale_ssh.ps1`이 있지만, 실행하면 다음과 같은 **보안·시스템 설정이 변경된다**.

- Windows OpenSSH Server 설치 및 서비스 자동 시작
- `sshd_config` 설정 변경 및 별도 백업
- **SSH 비밀번호 인증 활성화**
- OpenSSH 기본 방화벽 규칙 조정, Tailscale IP 대역용 SSH 방화벽 규칙 생성

그러므로 단순한 MAI 첫 설치 과정에서는 **이 스크립트를 실행하지 않는다.** SSH 접속이 꼭 필요하고 설정 변경을 이해한 사람만 프로젝트의 `scripts/setup_tailscale_ssh.ps1` 내용을 읽고 검토한 다음 관리자 PowerShell에서 수행한다. 가능하면 별도 SSH 키 인증과 최소 권한 정책을 계획한다.

---

## 16. 최종 체크리스트

아래 항목을 위에서부터 확인한다.

- [ ] Git을 설치했고 `git --version`이 출력된다.
- [ ] Python 3.11 이상을 설치했고 `python --version`이 출력된다.
- [ ] Ollama를 설치하고 모델을 하나 이상 내려받았다.
- [ ] `ollama list`에 `.env`의 `MAIN_MODEL`과 똑같은 모델명이 보인다.
- [ ] GitHub에서 프로젝트를 `git clone`으로 받았다.
- [ ] `git switch dev`로 원하는 개발 브랜치에 있다.
- [ ] `.venv` 가상환경을 생성하고 활성화했다.
- [ ] `python -m pip install -e .`가 성공했다.
- [ ] `python -m playwright install chromium`이 성공했다.
- [ ] Chromium 실행 테스트가 성공했다.
- [ ] `.env.example`을 복사해 `.env`를 만들었다.
- [ ] 소유자와 체험 계정의 **기본 비밀번호를 모두 변경했다**.
- [ ] 최초 로컬 테스트 시 `TAILSCALE_FUNNEL=false`로 설정했다.
- [ ] `python run_server.py`가 오류 없이 실행된다.
- [ ] `http://127.0.0.1:8000`에 로그인하고 모델 답변을 받았다.
- [ ] (브라우저 툴 포함 버전이라면) `browser_open` 테스트를 해봤다.
- [ ] (외부 접속을 원한다면) Tailscale을 설치하고 로그인했다.
- [ ] (외부 접속을 원한다면) 강한 비밀번호를 확인한 뒤 Funnel을 활성화했다.
- [ ] (외부 접속을 원한다면) 출력된 실제 `https://...ts.net` 주소로 접속에 성공했다.
- [ ] 매번 재실행하는 3개 명령과 서버 종료 방법을 알고 있다.

---

## 공식 참고 링크 및 프로젝트 문서

- Git 설치: https://git-scm.com/downloads/win
- GitHub 저장소: https://github.com/FoggyCaligo/MAI_MyAI_sllm
- Python 설치: https://www.python.org/downloads/windows/
- Python Windows 가이드: https://docs.python.org/3.11/using/windows.html
- Ollama 다운로드: https://ollama.com/download
- Ollama 모델 목록: https://ollama.com/library
- Playwright Chromium 설치: https://playwright.dev/python/docs/browsers
- Tailscale Windows 설치: https://tailscale.com/docs/install/windows
- Tailscale Funnel: https://tailscale.com/docs/features/tailscale-funnel
- Tailscale Funnel CLI: https://tailscale.com/docs/reference/tailscale-cli/funnel
- 프로젝트 `README.md`: 전체 아키텍처 및 기능
- 프로젝트 `RUN_UI.md`: Web UI와 계정, Tailscale 상세 자료


---

## Web UI 운영 상세 — 기존 RUN_UI.md 통합

아래는 기존 UI 실행 문서에서 다루던 계정, 지속 대화, 모델 선택, 업로드, 체험판 초기화, 테스트에 관한 운영 절차다. 최초 설치와 Tailscale 설정은 이 문서의 앞부분을 따른다.

### Owner / Trial 권한

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


### 로그인과 세션

Owner/Trial 모두 ID + password로 로그인한다. 성공하면 서버가 Bearer token을 발급한다.

같은 `user_id`로 새 로그인하면 기존 token을 폐기한다. 즉 **new-login-wins** 정책이다.

브라우저는 마지막으로 성공 로그인한 `user_id`만 localStorage에 기억한다. Password는 저장하지 않는다.

PC에서 사용하다 폰으로 같은 계정에 로그인하면 PC token이 무효화될 수 있다. PC로 돌아왔을 때 password만 다시 입력하면 된다.

로그인 실패 응답은 ID가 틀렸는지 password가 틀렸는지 구분해 노출하지 않는다.

---


### Persistent chat

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


### 모델 선택과 verifier

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


### 파일 업로드

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


### Trial 초기화

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


### 테스트

개발자가 변경 후 확인할 때는 전체 regression test를 실행한다.

```bash
python -m pytest -q
```

Runtime, auth, persistent chat, tool selection, verifier, memory, upload 변경 후에는 전체 suite를 기준으로 확인한다.


---

## 로컬 음성 입력 및 화자 검증 (선택)

기존 Owner/Trial/Family 계정 및 메모리 구조는 변경하지 않는다. 음성 입력 기능은 **Owner 로그인에만** 제공된다. 현재 로그인 Owner의 `db_id`별로 녹음한 목소리 특징을 따로 저장한다. 모델이 인식한 내용을 기존 메시지 입력란에 채운 후, 사용자가 확인하고 '전송'을 눌러야 일반 채팅으로 들어간다.

### 설치

가상환경에서 다음 명령을 실행한다.

```powershell
python -m pip install -e '.[voice]'
```

위 선택 의존성은 SpeechBrain 계열 화자 임베딩 라이브러리인 Resemblyzer와 로컬 Faster-Whisper를 설치한다. 모델 가중치는 최초 사용 시 내려받을 수 있으므로 **초기 다운로드에는 인터넷이 필요할 수 있지만**, 다운로드 후의 녹음·검증·음성 인식은 로컬에서 실행한다. CPU 사용량과 다운로드 용량에 주의한다.

### 환경 변수

```dotenv
VOICE_SILENCE_SECONDS=10
VOICE_MATCH_THRESHOLD=0.75
VOICE_WHISPER_MODEL=small
VOICE_PROFILE_DIR=./data/voice_profiles
```

무음이 `VOICE_SILENCE_SECONDS`만큼 지속되거나 다시 마이크를 누르면 녹음이 종료된다. 최대 120초까지 녹음할 수 있다. `VOICE_MATCH_THRESHOLD`는 유사도 판정 기준값으로서 환경·마이크·목소리 변화에 따른 오인식 가능성이 있다. 기준을 낮추면 다른 사람을 통과시킬 위험이 증가한다.

1. MAI의 **Owner 계정**으로 로그인한다.
2. 전송 버튼 오른쪽의 마이크를 처음 누르면 '목소리 등록' 버튼이 나타난다.
3. 목소리 등록을 누르고 최소 3초 이상 충분히 말을 한 다음 마이크를 다시 누른다. 무음 종료도 가능하다.
4. 등록이 완료되면 마이크를 클릭해 말하고, 음성 입력이 끝나면 텍스트를 확인하여 **전송**한다.
5. 마이크 사용에는 localhost 또는 HTTPS와 브라우저의 마이크 권한이 필요하다.

목소리 특징은 파일로 로컬 저장된다. 처음 등록한 목소리 벡터는 변경하지 않고, **화자 검증과 음성 인식을 모두 통과한 발화만** 적응형 특징 벡터에 소량 반영한다. 매 새 음성 구간마다 검증은 다시 수행하며, 한 번의 성공으로 이후 모든 음성을 무조건 신뢰하지 않는다. 현재 UI에서는 덮어쓰기/재등록을 지원하지 않는다. 재등록해야 한다면 먼저 저장소 관리 절차로 기존 프로필을 제거해야 한다.

### 보안 한계

이 기능은 타인의 일상적 음성 명령을 거르는 **편의 필터**일 뿐 신원 인증을 대체하지 않는다. 녹음 재생, AI 음성 복제, 음향적 유사성에 의한 오인증이 가능하다. Voice API는 기존 Bearer 로그인 인증을 추가로 요구하지만, 이미 로그인한 브라우저 및 서버의 로컬 터미널은 고위험 도구를 사용할 수 있으므로 이것만으로 완전한 접근 통제는 제공되지 않는다. 녹음된 원본은 처리 후 영구 저장하지 않고 특징 벡터만 프로필로 저장한다.


### 한국어와 영어를 섞어 말할 때

Faster-Whisper STT의 한국어 고정값을 제거하고 음성의 언어를 자동 감지한다. 한국어 문장에 영어 단어·제품 이름을 섞어 말할 수 있지만, 자동 감지는 주된 언어 위주로 수행되므로 영어 고유명사가 항상 정확히 받아써지는 것은 아니다. 인식된 입력란 내용을 확인하고 전송한다.

### 음성 모드 답변 읽기 (브라우저 TTS)

마이크를 눌러 음성 입력 모드를 켜면 마이크 아이콘은 빨간색으로 표시된다. 음성 녹음이 무음 종료된 뒤에도 음성 모드는 유지되어, 이후 MAI 답변이 **완료되었을 때만** 브라우저의 `speechSynthesis`로 답변을 읽는다. 마이크 버튼을 다시 눌러 음성 모드를 끄면 재생 중인 음성도 중지한다. 녹음 중 버튼을 눌러 종료하는 경우 음성 모드도 꺼진다. 이 변경은 자동으로 다음 발화를 다시 녹음하는 핸즈프리 루프를 제공하지 않는다.

브라우저·OS에 설치된 음성에 따라 한국어/영어 혼합 문장의 발음과 품질이 달라지며, TTS 지원 브라우저/목소리가 필요하다. 음성 답변은 브라우저 기능으로 생성되므로 외부 서버 TTS용 별도 API 키를 요구하지 않는다.
