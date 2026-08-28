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
OWNER_ID=my-owner-id
TRIAL_IDS=trial-a,trial-b
MAI_HOST=127.0.0.1
MAI_PORT=8000
TAILSCALE_SERVE=false
```

`OWNER_ID`는 필수다. `TRIAL_IDS`는 쉼표로 구분하며 비워둘 수 있다. 등록되지 않은 ID는 `/login` 단계에서 거부된다. 인증은 ID-only이며 로그인 성공 시 서버가 임시 Bearer session token을 발급한다. 브라우저를 닫거나 서버를 재시작한 뒤에는 다시 로그인한다.

하나의 `MEMORY_DB_PATH` 안에서도 User Anchor는 로그인 ID별로 분리된다. 기존 C 실험에서 `c-test`라는 사용자 ID로 기억을 만들었다면 `OWNER_ID=c-test` 또는 `TRIAL_IDS`에 `c-test`를 등록하고 같은 memory DB를 사용해야 해당 기억을 그대로 볼 수 있다.

## 2. 권한

Owner는 현재 등록된 모든 native tool을 사용할 수 있다.

```text
owner
  memory
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

한 브라우저 session에서는 짧은 대화 history를 유지한다. 장기기억은 persistent SQLite graph에 ID별 User Anchor로 저장되므로 session이 바뀌어도 memory tools로 다시 찾을 수 있다.

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
Access role → ToolRegistry composition
   ↓
Main LLM
   ↓ native tool selection
memory / file / code / web / market / terminal (role에 따라 노출)
   ↓
Final Response
   ↓
Post-response Memory Update
```

모델은 현재 제공된 native tool schema와 system capability contract를 보고 필요한 tool을 직접 선택한다. 특정 과거 정보는 `memory_recall(query)`, 특정 주제 없이 넓은 기억 개요가 필요하면 `memory_overview`, 추가 graph 탐색은 `memory_search(node_id)`를 사용할 수 있다.
