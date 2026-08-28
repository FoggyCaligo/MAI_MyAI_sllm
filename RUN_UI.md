# MAI Web UI / Tailscale 실행

이 문서는 pure-agent C를 기본 runtime으로 사용하는 로컬 Web UI 실행 방법을 기록한다.

## 1. 설치

```bash
source .venv/Scripts/activate
python -m pip install -e ".[dev]"
```

`.env.example`을 `.env`로 복사한 뒤 사용할 모델을 지정한다.

```env
MAIN_MODEL=gemma4:e4b
MAI_USER_ID=local-user
MEMORY_DB_PATH=./data/memory.sqlite3
SENTENCE_BREAKER_DB_PATH=./data/sentence_breaker.sqlite3
MAI_HOST=127.0.0.1
MAI_PORT=8000
TAILSCALE_SERVE=false
```

## 2. 로컬 UI

```bash
python run_server.py
```

브라우저에서 다음 주소를 연다.

```text
http://127.0.0.1:8000
```

UI의 한 브라우저 세션에서는 짧은 대화 history를 유지한다. 장기기억은 별도의 persistent SQLite graph에 저장되므로 서버/브라우저 세션이 바뀌어도 `memory_recall`을 통해 다시 찾을 수 있다.

## 3. Tailscale Serve

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

Windows에서 Tailscale Serve 설정에 관리자 권한이 필요한 환경이라면 관리자 Terminal에서 서버를 실행한다.

## 4. 현재 C runtime

기본 요청 경로에는 Tool Requirement Preflight와 automatic recall이 없다.

```text
User Input
   ↓
Main LLM
   ↓ native tool selection
memory_recall / memory_search / file / code / terminal / ...
   ↓
Final Response
   ↓
Post-response Memory Update
```

모델은 현재 제공된 native tool schema와 system capability contract를 보고 필요한 tool을 직접 선택한다. 개인 장기기억이 필요하면 `memory_recall(query)`가 최초 진입점이며, 추가 graph 탐색은 `memory_search(node_id)`를 사용한다.
