# MAI Web UI / Tailscale 실행

이 문서는 pure-agent C를 기본 runtime으로 사용하는 로컬 Web UI 실행 방법을 기록한다.

## 1. 설치

```bash
source .venv/Scripts/activate
python -m pip install -e ".[dev]"
```

`.env.example`을 `.env`로 복사한 뒤 기본 모델, memory DB, 허용 ID를 지정한다.

```env
MAIN_MODEL=gemma4:e4b
VISION_MODEL=
MEMORY_DB_PATH=./data/memory.sqlite3
SENTENCE_BREAKER_DB_PATH=./data/sentence_breaker.sqlite3
OWNER_ID=my-owner-login
OWNER_MEMORY_ID=local-user
TRIAL_IDS=trial-a,trial-b
MAI_HOST=127.0.0.1
MAI_PORT=8000
TAILSCALE_SERVE=false
```

`OWNER_ID`와 `OWNER_MEMORY_ID`는 필수다. `OWNER_ID`는 Web UI 로그인에 사용하는 인증 ID이고, `OWNER_MEMORY_ID`는 owner가 graph memory에서 사용할 User Anchor identity다. 둘은 같아도 되지만 같은 개념으로 취급하지 않는다.

기존 버전에서 `MAI_USER_ID=local-user`로 기억을 쌓았다면 새 설정은 다음처럼 두면 된다.

```env
OWNER_ID=원하는-로그인-ID
OWNER_MEMORY_ID=local-user
```

`VISION_MODEL`은 선택 사항이다. 비워두면 `image_analyze` 자체가 ToolRegistry에 노출되지 않는다. 이미지 분석을 사용하려면 Ollama에 설치되어 있고 이미지 입력을 지원하는 모델 이름을 지정한다.

`TRIAL_IDS`는 쉼표로 구분하며 비워둘 수 있다. Trial은 별도의 memory mapping 설정 없이 각 로그인 ID 자체를 자신의 memory identity로 사용한다. `OWNER_MEMORY_ID`가 trial ID와 충돌하면 startup이 실패한다.

등록되지 않은 ID는 `/login` 단계에서 거부된다. 인증은 ID-only이며 로그인 성공 시 서버가 임시 Bearer session token을 발급한다.

## 2. 권한과 읽기 도구

Owner는 현재 등록된 모든 native tool을 사용할 수 있다. Trial은 파일 변경/삭제와 terminal을 받지 않지만 읽기 계열과 외부 정보 계열은 사용할 수 있다.

```text
공통 read/info capability
  memory_recall / memory_overview / memory_search
  current_time
  file_list / file_search / file_read
  code_search / code_read / code_symbols
  document_read
  image_analyze          # VISION_MODEL이 설정된 경우
  web_search / web_fetch
  market_data

owner 추가 capability
  file_write / file_create / file_delete / file_move / file_copy
  terminal_run
```

`document_read`는 PDF, DOCX, XLSX, PPTX를 지원한다. 큰 결과는 `max_chars`로 제한할 수 있다.

`web_search`는 검색 결과를 찾는 도구이고 `web_fetch`는 이미 알고 있는 특정 public HTTP(S) URL의 본문을 읽는 도구다. `web_fetch`는 loopback/private-network 주소를 거부하고 redirect 대상도 다시 검사한다.

## 3. 로컬 UI

```bash
python run_server.py
```

브라우저에서 다음 주소를 연다.

```text
http://127.0.0.1:8000
```

처음에는 ID login 화면만 보인다. 허용 ID로 로그인한 뒤 Ollama 설치 모델을 상단 selector에서 선택할 수 있다. 선택한 모델은 해당 요청에 직접 사용되며 `MAIN_MODEL`은 기본 선택값이다.

한 브라우저 session에서는 짧은 대화 history를 유지한다. Chat history는 인증 ID별로 분리되고, 장기기억은 persistent SQLite graph에서 `memory_user_id`별 User Anchor로 저장된다.

응답은 기본 Markdown 요소를 렌더링하며, 각 응답 아래의 `tool log`에서 tool name, 성공/실패 상태, arguments, result를 확인할 수 있다. 새 메시지와 응답이 추가되면 message pane은 자동으로 최하단으로 스크롤된다.

## 4. Tailscale Serve

`.env`에서 다음을 켠다.

```env
TAILSCALE_SERVE=true
```

그 뒤 동일하게 실행한다.

```bash
python run_server.py
```

MAI는 `tailscale serve <MAI_PORT>`를 foreground child process로 실행한다. Tailscale CLI가 표시하는 tailnet URL로 다른 Tailscale 기기에서 접속한다. 서버가 정상 종료되면 해당 Serve child process도 종료한다.

Tailscale 실행 파일이 PATH에 없거나 Serve 권한/설정 문제로 command가 즉시 실패하면 MAI startup도 실패한다. 이를 성공처럼 우회하지 않는다.

## 5. 현재 C runtime

기본 요청 경로에는 Tool Requirement Preflight와 automatic recall이 없다.

```text
Authenticated User
   ↓
Access identity → role + memory identity
   ↓
Access role → ToolRegistry composition
   ↓
Main LLM
   ↓ native tool selection
memory / time / file / code / document / image / web / market / terminal
   ↓
Final Response
   ↓
Post-response Memory Update (memory identity 기준)
```

모델은 현재 제공된 native tool schema와 system capability contract를 보고 필요한 tool을 직접 선택한다. 특정 과거 정보는 `memory_recall(query)`, 특정 주제 없이 넓은 기억 개요가 필요하면 `memory_overview`, 추가 graph 탐색은 `memory_search(node_id)`를 사용할 수 있다.
