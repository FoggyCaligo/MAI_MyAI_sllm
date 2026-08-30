# MAI Web UI / Tailscale 실행

이 문서는 현재 production Web UI의 설치, 계정, persistent chat, model access, Tailscale Funnel 실행 방법을 기록한다.

---

## 1. 설치

```bash
source .venv/Scripts/activate
python -m pip install -e ".[dev]"
```

`.env.example`을 `.env`로 복사한 뒤 실제 계정과 환경에 맞게 수정한다.

기본 예시:

```env
OLLAMA_HOST=http://127.0.0.1:11434
MAIN_MODEL=gemma4:e4b
VISION_MODEL=

MEMORY_DB_PATH=./data/memory.sqlite3
SENTENCE_BREAKER_DB_PATH=./data/sentence_breaker.sqlite3
CHAT_DB_PATH=./data/chat.sqlite3

OWNER_USERS=[{"user_id":"owner","user_pw":"change-me","db_id":"local-user"}]
TRIAL_USERS=[{"user_id":"체험판","user_pw":"0000","db_id":"trial-default"}]

MAI_HOST=127.0.0.1
MAI_PORT=8000
SESSION_HISTORY_MESSAGES=24
TAILSCALE_FUNNEL=false
```

`.env`는 Git에 올리지 않는다. `user_pw`가 현재 설계상 plaintext이기 때문이다.

---

## 2. user_info 구조

Owner와 Trial 모두 정확히 세 string field를 갖는 record로 정의한다.

```text
user_id
  Web UI 로그인 ID
  변경 가능

user_pw
  로그인 비밀번호
  local .env에 plaintext 저장

db_id
  stable persistent identity
  memory / Web chat / trial upload ownership 기준
```

예를 들어 기존 memory graph가 `local-user`로 쌓여 있다면 로그인 ID를 바꿔도 `db_id`만 유지하면 된다.

```env
OWNER_USERS=[{"user_id":"new-login-name","user_pw":"new-password","db_id":"local-user"}]
```

이 경우 memory DB migration 없이 기존 `local-user` 기억과 Web chat을 계속 사용한다.

모든 `user_id`는 고유해야 하고 모든 `db_id`도 고유해야 한다. 다른 계정의 `user_id`와 `db_id`가 교차 충돌하는 설정도 startup에서 거부한다.

Legacy `OWNER_ID`, `OWNER_MEMORY_ID`, `OWNER_ACCOUNTS`, `TRIAL_IDS`는 fallback으로 사용하지 않는다.

---

## 3. 로그인과 세션

Owner/Trial 모두 ID + password로 로그인한다. 성공하면 서버가 Bearer token을 발급한다.

같은 `user_id`로 새 로그인하면 기존 token을 폐기한다. 즉 **new-login-wins** 정책이다.

브라우저는 마지막으로 성공 로그인한 `user_id`만 localStorage에 기억한다. Password는 저장하지 않는다.

따라서 PC에서 사용하다 폰으로 같은 계정에 로그인해 PC token이 무효화되어도, PC로 돌아오면 ID는 이미 입력된 상태이고 password만 다시 입력하면 된다.

로그인 실패 응답은 ID가 틀렸는지 password가 틀렸는지 구분해 노출하지 않는다.

---

## 4. Persistent chat

대화 기록은 로그인 ID가 아니라 `db_id` 기준으로 `CHAT_DB_PATH`의 SQLite에 저장된다.

Web UI 전용 table:

```text
web_chat_messages
```

기존 SQLite 파일 안에 다른 용도의 `chat_messages`가 있어도 함부로 변형하지 않는다.

Startup migration 규칙:

- `web_chat_messages`가 없고 기존 `chat_messages`가 과거 persistent-chat의 정확히 알려진 schema이면 구조적으로 이관
- 기존 `chat_messages`가 unrelated schema이면 그대로 보존하고 별도 `web_chat_messages` 생성
- `web_chat_messages` 자체가 알 수 없는 schema이면 startup failure

따라서 기존 `data/chat.sqlite3`를 업그레이드 때문에 삭제할 필요가 없다.

`SESSION_HISTORY_MESSAGES`는 model conversational context와 Web UI 복원 범위에 동일하게 적용된다. 기본값 `24`이면 model에게 최근 24 messages를 전달하고, 새로 로그인하거나 페이지를 다시 열었을 때 화면에도 같은 최근 24 messages만 복원한다. 더 오래된 기록은 SQLite에 남아 있지만 현재 UI/model context에는 포함하지 않는다.

Browser/device가 사라져도 server process가 살아 있으면 running resumable chat job은 계속될 수 있다. 완료된 assistant answer는 persistent chat에 저장되어 다음 접속에서 보인다.

단, running job 자체는 process-memory state이므로 server process restart 이후 계산을 이어서 수행하는 durable queue는 아니다.

---

## 5. 모델 선택

기본 `MAIN_MODEL`은 다음과 같다.

```env
MAIN_MODEL=gemma4:e4b
```

여러 계정의 동시 사용을 고려해 가벼운 모델을 기본으로 둔다.

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

---

## 6. Tool requirement preflight와 final verifier

각 user request는 main agent 전에 model-based tool requirement preflight를 거친다.

Preflight는 user request, 최근 대화, 현재 available tool schema를 보고 이번 요청에 반드시 필요한 native tool을 structured output으로 선택한다. 선택된 required tools는 해당 run 동안 frozen된다.

Main agent가 required tool 없이 final을 시도하면 correction round가 발생한다.

Final answer는 release 전에 verifier를 거친다. 현재 주요 검증 축:

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

## 7. Failure recovery

Main planner/agent가 fatal failure로 끝나면, 이미 확보된 tool evidence를 이용해 `FailureAnswerFinalizer`가 한 번 user-visible recovery answer를 만들 수 있다.

이 recovery는 tool을 추가 호출하지 않으며:

- 실패를 숨기지 않고
- 성공하지 않은 결과를 성공했다고 하지 않으며
- 확인된 부분과 실패/미확인 부분을 구분하고
- 가능한 useful partial answer를 반환한다.

Recovery 자체도 실패하면 원래 failure가 드러난다.

---

## 8. Owner / Trial tool 권한

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

## 9. 파일 업로드

기본 upload root:

```text
./mai_uploads/
```

필요하면:

```env
MAI_UPLOAD_ROOT=./mai_uploads
```

Owner는 upload root를 사용하고 Trial은 `db_id`에서 파생된 전용 directory를 사용한다.

같은 account directory에서 같은 filename을 조용히 overwrite하지 않고 HTTP 409로 실패한다. Filename에 path separator를 허용하지 않는다.

업로드된 파일의 실제 path가 input에 추가되므로 model이 `file_read`, `document_read`, `image_analyze` 등을 사용할 수 있다.

---

## 10. Trial 초기화

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

## 11. 로컬 실행

```bash
python run_server.py
```

브라우저:

```text
http://127.0.0.1:8000
```

---

## 12. Tailscale Funnel

`.env`:

```env
TAILSCALE_FUNNEL=true
```

실행:

```bash
python run_server.py
```

MAI는 Tailscale background Funnel을 구성하고 public URL / proxy status를 terminal에 출력한다.

Legacy `TAILSCALE_SERVE=true`는 retired configuration이다. True로 남아 있으면 다른 방식으로 silent fallback하지 않고 startup에서 실패한다.

---

## 13. 테스트

전체 regression test:

```bash
python -m pytest -q
```

Runtime, auth, persistent chat, tool preflight, verifier, memory, upload 변경 후에는 전체 suite를 기준으로 확인한다.
