# MAI Web UI / Tailscale 실행

이 문서는 pure-agent C production Web UI의 실행과 계정 설정 방법을 기록한다.

## 1. 설치와 계정 설정

```bash
source .venv/Scripts/activate
python -m pip install -e ".[dev]"
```

`.env.example`을 `.env`로 복사한다.

```env
MAIN_MODEL=gemma4:e4b
VISION_MODEL=
MEMORY_DB_PATH=./data/memory.sqlite3
SENTENCE_BREAKER_DB_PATH=./data/sentence_breaker.sqlite3
CHAT_DB_PATH=./data/chat.sqlite3

OWNER_USERS=[{"user_id":"owner","user_pw":"내비밀번호","db_id":"local-user"}]
TRIAL_USERS=[{"user_id":"체험판","user_pw":"0000","db_id":"trial-default"}]

MAI_HOST=127.0.0.1
MAI_PORT=8000
TAILSCALE_FUNNEL=false
```

Owner와 Trial 모두 각 계정을 하나의 `user_info` record로 정의한다. record에는 정확히 세 필드가 있다.

```text
user_id
  Web UI 로그인 ID
  나중에 변경 가능

user_pw
  Web UI 로그인 비밀번호
  현재 설계에서는 .env에 평문 문자열로 저장

db_id
  memory/chat 등 persistent data의 내부 identity
  로그인 ID를 바꾸더라도 기존 데이터를 이어 쓰려면 그대로 유지
```

예를 들어 기존 memory graph가 `local-user`로 쌓여 있다면 로그인 ID를 새로 정해도 DB migration은 필요 없다.

```env
OWNER_USERS=[{"user_id":"원하는-로그인-ID","user_pw":"내비밀번호","db_id":"local-user"}]
```

이후 로그인 ID를 다시 바꾸고 싶다면 `user_id`만 바꾸고 `db_id`는 유지한다.

```env
OWNER_USERS=[{"user_id":"new-login-name","user_pw":"내비밀번호","db_id":"local-user"}]
```

그러면 기존 `local-user` memory와 Web UI chat history는 계속 같은 계정 데이터로 연결된다.

`OWNER_USERS`에는 여러 owner를 넣을 수 있고 `TRIAL_USERS`에도 여러 trial을 넣을 수 있다. 모든 `user_id`는 계정 간 고유해야 하고 모든 `db_id`도 고유해야 한다. 다른 계정의 `user_id`와 `db_id`가 교차 충돌하는 설정도 startup에서 거부한다.

기존 `OWNER_ID`, `OWNER_MEMORY_ID`, `OWNER_ACCOUNTS`, `TRIAL_IDS`는 새 인증 계약의 fallback으로 사용하지 않는다. `OWNER_USERS`가 없으면 startup이 명확하게 실패한다.

`user_pw`는 요청대로 평문이다. `.env`는 Git에 올리지 않아야 한다. 로그인 실패 응답은 ID가 틀렸는지 비밀번호가 틀렸는지 구분하지 않는다.

`VISION_MODEL`은 선택 사항이다. 비워두면 `image_analyze`가 ToolRegistry에 노출되지 않는다.

## 2. 로그인과 세션

Owner/Trial 모두 ID + 비밀번호로 로그인한다. 성공하면 서버가 임시 Bearer token을 발급한다.

같은 `user_id`로 새 로그인하면 기존 token은 즉시 폐기된다. 즉 한 계정은 한쪽 로그인만 유지하며 새 로그인이 우선한다.

브라우저는 마지막으로 성공 로그인한 `user_id`만 localStorage에 기억한다. 다른 기기 로그인으로 현재 session이 끊기거나 브라우저를 닫았다 다시 열어도 ID 입력란은 복원된다. 비밀번호는 저장하지 않으므로 다시 접속할 때는 비밀번호만 입력하면 된다.

대화 기록은 로그인 ID가 아니라 `db_id` 기준으로 `CHAT_DB_PATH`의 SQLite에 저장된다. 따라서 브라우저나 폰을 닫았다가 다시 접속해도 같은 `db_id`의 기존 conversation을 복원할 수 있다. background chat job이 사용자가 자리를 비운 동안 끝났다면 완료된 assistant answer도 persistent chat history에 저장되어 다음 접속 때 표시된다.

### Chat DB 테이블과 기존 DB 호환

`CHAT_DB_PATH`는 기존 MAI가 이미 사용하던 SQLite 파일을 가리킬 수도 있으므로, Web UI의 persistent conversation은 일반적인 `chat_messages`가 아니라 전용 `web_chat_messages` 테이블에 저장한다.

startup 시 다음 규칙을 사용한다.

- `web_chat_messages`가 없고, 기존 `chat_messages`가 #134/#135 persistent-chat이 만든 **정확히 알려진 schema**라면 `web_chat_messages`로 구조적으로 이관한다.
- 기존 `chat_messages`가 다른 schema라면 다른 MAI 데이터로 간주해 수정하거나 삭제하지 않고 그대로 보존한다.
- 그 경우 Web UI는 같은 SQLite 파일 안에 별도의 `web_chat_messages`를 새로 만든다.
- `web_chat_messages` 자체가 알 수 없는 schema라면 조용히 우회하지 않고 startup에서 명확하게 실패한다.

따라서 업그레이드를 위해 기존 `data/chat.sqlite3`를 삭제할 필요가 없다. 기존 데이터가 있는 파일을 그대로 둔 채 새 버전의 `python run_server.py`를 실행하면 된다.

모델에게 전달하는 문맥은 전체 UI 기록과 별개로 최근 `SESSION_HISTORY_MESSAGES`개로 제한할 수 있다.

## 3. 모델 선택

`.env.example`의 기본 `MAIN_MODEL`은 여러 계정의 동시 사용을 고려해 `gemma4:e4b`로 둔다. Owner는 설치된 다른 Ollama 모델을 선택할 수 있지만 Trial은 `MAIN_MODEL`로 고정된다.

```text
owner
  GET /models -> 설치된 Ollama model 전체
  /chat model -> 선택 가능

trial
  GET /models -> MAIN_MODEL 하나만
  /chat model -> MAIN_MODEL로 고정
```

Trial client가 UI를 우회해 다른 model을 직접 요청해도 서버가 HTTP 403으로 거부한다.

## 4. 권한과 도구

Owner는 전체 native tool을 사용할 수 있다. Trial은 arbitrary local mutation과 terminal을 받지 않지만 읽기/외부 정보 계열은 사용할 수 있다.

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
  file_write / file_create   # 자기 upload directory 안에서만
```

Trial upload directory도 `db_id`에서 파생된다. 따라서 Trial의 `user_id`를 바꿔도 같은 `db_id`를 유지하면 기존 upload directory가 그대로 연결된다.

## 5. 로컬 UI 실행

```bash
python run_server.py
```

브라우저:

```text
http://127.0.0.1:8000
```

로그인 화면에는 ID와 비밀번호 입력란이 표시된다. 비밀번호는 로그인 요청에만 사용하며 browser storage에 별도로 저장하지 않는다.

파일 업로드 root 기본값은 `./mai_uploads/`이다. 같은 계정 폴더에서 같은 이름을 조용히 덮어쓰지 않고 HTTP 409로 실패한다.

## 6. Trial 계정 초기화

기본 `.env.example`의 체험 계정은 다음과 같다.

```text
ID: 체험판
PW: 0000
```

Trial ID를 다른 사람에게 재사용하려면 서버를 먼저 종료하고 `reset_trial.py`를 사용한다.

```bash
python reset_trial.py 체험판 --dry-run
python reset_trial.py 체험판
```

초기화 대상은 해당 Trial의 `user_id`로 계정을 찾은 뒤, 실제 persistent memory/chat/upload ownership은 그 계정의 `db_id`를 기준으로 삭제한다. Web UI chat은 `web_chat_messages`만 대상으로 하며, schema가 다른 기존 `chat_messages` 테이블은 건드리지 않는다.

## 7. Tailscale Funnel

`.env`:

```env
TAILSCALE_FUNNEL=true
```

실행:

```bash
python run_server.py
```

MAI는 `tailscale funnel --bg --yes <MAI_PORT>`에 해당하는 공개 Funnel을 설정하고 Tailscale이 보고하는 public URL과 proxy mapping을 출력한다. `TAILSCALE_SERVE`는 폐기된 설정이며 true로 남아 있으면 startup에서 실패한다.

## 8. Identity 흐름

```text
.env user_info
  ├─ user_id  -> 로그인 / 현재 session token 식별
  ├─ user_pw  -> 로그인 인증
  └─ db_id    -> stable persistent identity
                   ├─ Memory User Anchor
                   ├─ Web UI Chat history
                   └─ Trial upload ownership

Authenticated Principal
  ↓ role
ToolRegistry permissions
  ↓
Main LLM + native tools
  ↓
Final verification
  ↓
Final response + background memory update(db_id)
```

핵심 계약은 `user_id`를 이름표처럼 변경 가능하게 두고, 실제 장기 데이터 연결은 `db_id`가 담당한다는 것이다.
