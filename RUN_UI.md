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

이렇게 하면 로그인 ID를 새로 정해도 기존 `local-user` User Anchor의 기억을 그대로 읽고 계속 기록한다. 기존 C 실험이 `c-test` identity를 사용했다면 `OWNER_MEMORY_ID=c-test`로 지정한다.

`TRIAL_IDS`는 쉼표로 구분하며 비워둘 수 있다. Trial은 별도의 memory mapping 설정 없이 각 로그인 ID 자체를 자신의 memory identity로 사용한다. `OWNER_MEMORY_ID`가 trial ID와 충돌하면 startup이 실패하여 서로 다른 계정이 같은 User Anchor를 공유하지 못하게 한다.

등록되지 않은 ID는 `/login` 단계에서 거부된다. 인증은 ID-only이며 로그인 성공 시 서버가 임시 Bearer session token을 발급한다. 브라우저를 닫거나 서버를 재시작한 뒤에는 다시 로그인한다.

## 2. 권한

Owner는 현재 등록된 모든 native tool을 사용할 수 있다.

```text
owner
  memory
  current_time
  filesystem read/write/create/delete/move/copy
  code read/search/symbols
  terminal
  web_search
  market_data
```

Trial은 runtime이 처음부터 제한된 ToolRegistry를 구성한다. 프롬프트로 사용을 자제시키는 방식이 아니다.

```text
trial
  memory_recall / memory_overview / memory_search
  current_time
  file_list / file_search / file_read
  code_search / code_read / code_symbols
  web_search / market_data
```

Trial에는 `file_write`, `file_create`, `file_delete`, `file_move`, `file_copy`, `terminal_run`이 노출되지 않는다.

## 3. 로컬 UI

```bash
python run_server.py
```

브라우저에서 다음 주소를 연다.

```text
http://127.0.0.1:8000
```

처음에는 ID login 화면만 보인다. 허용 ID로 로그인한 뒤 Ollama 설치 모델을 상단 selector에서 선택할 수 있다. 선택한 모델은 해당 요청에 직접 사용되며 `MAIN_MODEL`은 기본 선택값이다.

한 브라우저 session에서는 짧은 대화 history를 유지한다. Chat history는 인증 ID별로 분리되고, 장기기억은 persistent SQLite graph에서 `memory_user_id`별 User Anchor로 저장된다. 따라서 owner의 로그인 이름을 바꾸더라도 `OWNER_MEMORY_ID`를 유지하면 같은 장기기억을 계속 사용할 수 있다.

응답은 기본 Markdown 요소를 렌더링하며, 각 응답 아래의 `tool log`에서 tool name, 성공/실패 상태, arguments, result를 확인할 수 있다. 새 메시지와 응답이 추가되면 message pane은 자동으로 최하단으로 스크롤된다. 응답 대기 중에는 이전 MK5와 같은 세 점 bounce loader를 표시한다.

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
memory / time / file / code / web / market / terminal (role에 따라 노출)
   ↓
Final Response
   ↓
Post-response Memory Update (memory identity 기준)
```

모델은 현재 제공된 native tool schema와 system capability contract를 보고 필요한 tool을 직접 선택한다. 특정 과거 정보는 `memory_recall(query)`, 특정 주제 없이 넓은 기억 개요가 필요하면 `memory_overview`, 추가 graph 탐색은 `memory_search(node_id)`를 사용할 수 있다.
