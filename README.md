# MAI MyAI sLLM

**사용자를 장기적으로 기억하고, 그 기억을 바탕으로 대화·검색·문서 이해·로컬 PC 작업까지 이어 가는 로컬 sLLM 개인 에이전트 런타임**이다.

이 README는 다른 MACHI/MK 문서를 읽지 않아도 현재 MAI의 목적, 장기기억 구조, production runtime, 권한 모델, 도구와 실행 방법을 이해할 수 있도록 작성한다. 메모리 세부 schema는 [`MEMORY_V1.md`](MEMORY_V1.md), 구현 계약은 [`WORKING_CONTRACT.md`](WORKING_CONTRACT.md), Web UI와 Tailscale 실행은 [`RUN_UI.md`](RUN_UI.md)를 기준으로 한다.

---

# 1. 프로젝트 정의

일반적인 대화형 LLM의 context는 한 대화 안에서는 강하지만, 장기간 사용자를 기억하고 그 기억의 근거까지 보존하는 일은 별도의 문제다.

MAI는 장기기억을 특정 모델이나 서비스 계정 내부에 맡기지 않는다. 사용자 발화와 그로부터 만들어진 구조를 로컬 SQLite graph에 저장하고, 필요할 때 모델이 native tool로 해당 기억을 직접 불러온다.

```text
User
  ↓
MAI Web / API
  ↓
Authenticated Principal
  ├─ auth identity
  └─ memory identity
  ↓
Pure-agent Runtime
  ↓
Local Ollama Model
  ↓ native tool calls
Memory / Time / Files / Code / Documents / Images / Web / Market / Terminal
  ↓
Final Response
  ↓
Post-response Memory Write
```

LLM은 답변 생성, 상황 판단, tool 선택을 담당한다. 장기기억의 본체는 LLM 안에 있지 않으므로 메인 모델을 교체해도 memory DB 자체는 유지된다.

---

# 2. 현재 production runtime

현재 기본 실행 경로는 **pure-agent C 구조**다.

```text
User Input
  ↓
Main Agent
  ↓
native tool selection
  ↓
ToolRegistry execution
  ↓
Final Response
  ↓
MemoryRuntime.finish_turn()
```

production 경로에는 별도의 Tool Requirement Preflight나 automatic recall이 없다. 모델은 현재 노출된 native tool schema를 보고 필요한 기능을 스스로 선택한다.

기억이 필요하면 다음 tool을 명시적으로 사용할 수 있다.

- `memory_overview(limit)`: 특정 검색어 없이 사용자의 넓은 기억 개요를 본다.
- `memory_recall(query)`: 특정 주제의 기억을 검색한다.
- `memory_search(node_id)`: 선택한 memory node 주변을 one-hop 확장한다.

이 구조는 framework가 사용자 문장의 문자열 패턴을 보고 tool 필요 여부를 정하는 방식이 아니다. 의미 판단은 모델이 하고, runtime은 schema·권한·실행·실패 계약을 강제한다.

---

# 3. Graph Long-term Memory

MAI의 핵심은 검색 가능한 대화 로그가 아니라 **근거와 관계를 보존하는 장기기억 graph**다.

```text
User Anchor
   └─spoke→ Utterance
                  ├─mentions→ Concept
                  └─derived_fact→ Fact
                                      └─mentions→ Concept
```

핵심 node는 다음과 같다.

- **User Anchor**: memory identity마다 하나씩 존재하는 사용자 기준점
- **Utterance**: 사용자의 원문 evidence
- **Fact**: 사용자 발화에서 파생된 의미 단위
- **Concept**: Sentence_Breaker가 만든 재사용 가능한 개념 단위

원문은 Fact로 덮어쓰지 않는다. Fact가 있다면 어떤 Utterance에서 나왔는지 provenance를 유지한다.

현재 Concept retrieval은 embedding/vector DB에 의존하지 않는다.

```text
query
  ↓ Sentence_Breaker
canonical segments
  ↓
Exact hash lookup
  ↓ miss
SQLite FTS5 lexical search
  ↓
Concept Node
  ↓
Graph neighborhood / User Anchor path
```

따라서 메인 LLM을 교체했다고 해서 embedding 좌표계를 다시 만들 필요가 없다. FTS5 검색 결과는 Concept identity를 새로 정의하지 않으며, Concept identity는 Sentence_Breaker의 canonical segment가 결정한다.

현재 production memory write는 최종 응답 뒤 별도 lifecycle에서 실행한다. raw user evidence와 Concept graph는 저장되며, model-backed `FactExtractor`는 아직 production runtime에 연결되어 있지 않다. 이 상태를 fallback으로 숨기지 않는다.

---

# 4. Native Tool Agent

MAI는 Ollama native `tools` / `tool_calls`를 직접 사용한다. tool call을 임의 문자열 포맷으로 다시 만들거나 `if text contains ...` 규칙으로 route하지 않는다.

현재 주요 tool은 다음과 같다.

| 범주 | 도구 | 역할 |
|---|---|---|
| Memory | `memory_overview`, `memory_recall`, `memory_search` | 장기기억 조회·확장 |
| Time | `current_time` | host OS의 현재 local/UTC 시간 조회 |
| Filesystem | `file_list`, `file_search`, `file_read` | PC 파일 탐색과 읽기 |
| Filesystem mutation | `file_write`, `file_create`, `file_delete`, `file_move`, `file_copy` | 파일 생성·수정·삭제·이동·복사 |
| Code | `code_search`, `code_read`, `code_symbols` | 코드 검색·line read·Python AST symbol 탐색 |
| Document | `document_read` | PDF, DOCX, XLSX, CSV, PPTX 읽기 |
| Image | `image_analyze` | 별도 Ollama vision model을 이용한 이미지 분석 |
| Web | `web_search`, `web_fetch` | 웹 검색과 public URL 본문 읽기 |
| Market | `market_data` | 한국 상장주식의 현재 시세/밸류에이션 조회 |
| Terminal | `terminal_run` | 로컬 shell command 실행 |

`image_analyze`는 `.env`의 `VISION_MODEL`이 설정된 경우에만 ToolRegistry에 등록된다.

`web_fetch`는 public HTTP(S) URL만 읽으며 loopback/private-network 주소와 redirect 목적지를 검사한다. 내부망 접근을 웹 도구로 우회하지 않는다.

파일·코드·문서·이미지·터미널 기능은 owner 기준으로 repository 내부에 제한되지 않는다. MAI 프로세스를 실행한 OS 계정이 접근 가능한 경로를 절대경로로 다룰 수 있다.

---

# 5. 사용자 인증과 memory identity

Web UI 인증은 현재 **ID-only** 방식이다.

```env
OWNER_ID=my-owner-login
OWNER_MEMORY_ID=local-user
TRIAL_IDS=trial-a,trial-b
```

`OWNER_ID`는 로그인 identity이고 `OWNER_MEMORY_ID`는 graph memory의 User Anchor identity다. 둘은 의도적으로 분리되어 있다.

기존 버전에서 `local-user`라는 User Anchor로 기억을 저장했다면 로그인 ID가 달라져도 다음처럼 기존 기억을 계속 사용할 수 있다.

```env
OWNER_ID=새로운-로그인-ID
OWNER_MEMORY_ID=local-user
```

Trial은 각 로그인 ID 자체를 memory identity로 사용한다. Owner memory identity와 trial identity가 충돌하면 startup이 실패한다.

---

# 6. Owner / Trial 권한

권한은 prompt가 아니라 서버와 ToolRegistry/handler 경계에서 강제한다.

### Owner

Owner는 설치된 Ollama 모델 중 원하는 모델을 선택할 수 있고 현재 등록된 전체 도구를 사용할 수 있다.

### Trial

Trial은 읽기·검색·기억·웹·시장·시간 기능을 사용할 수 있지만 arbitrary PC mutation과 terminal은 사용할 수 없다.

```text
Trial 공통 기능
  memory_overview / memory_recall / memory_search
  current_time
  file_list / file_search / file_read
  code_search / code_read / code_symbols
  document_read
  image_analyze          # VISION_MODEL 설정 시
  web_search / web_fetch
  market_data

Trial 제한 mutation
  file_write / file_create
    └─ 자기 계정의 전용 upload directory 안에서만 허용

Trial 미노출
  file_delete / file_move / file_copy
  terminal_run
```

Trial upload directory는 계정끼리 충돌하거나 덮어쓰지 않도록 분리된다. raw trial ID를 경로 이름으로 직접 사용하지 않고 SHA-256 기반의 안정적인 path-safe directory key를 사용한다.

```text
./mai_uploads/
└─ trials/
   ├─ <trial-a의 hash>/
   └─ <trial-b의 hash>/
```

Trial의 model은 `MAIN_MODEL`로 고정된다. `/models`도 trial에게 해당 모델 하나만 반환하고, client가 직접 다른 model을 POST해도 서버가 HTTP 403으로 거부한다.

`MAIN_MODEL` 기본값은 현재 `ornith-1.5:9b`이며, 설정을 바꾸면 trial의 고정 모델도 함께 바뀐다.

---

# 7. Web UI와 파일 업로드

`python run_server.py`로 FastAPI Web UI를 실행한다.

```text
http://127.0.0.1:8000
```

Web UI에는 ID login, owner model selector, Markdown response, expandable tool log, 자동 스크롤, 파일 업로드가 포함되어 있다.

입력창의 `＋` 버튼으로 owner/trial 모두 파일을 업로드할 수 있다. 기본 upload root는 다음과 같다.

```text
./mai_uploads/
```

필요하면 `.env`에서 바꿀 수 있다.

```env
MAI_UPLOAD_ROOT=./mai_uploads
```

Owner는 기존처럼 upload root를 사용한다. Trial은 각 계정별 전용 하위 폴더를 사용하며, `file_write`와 `file_create`도 같은 전용 폴더 안으로 제한된다.

`mai_uploads/`는 `.gitignore`에 포함되어 Git에 올라가지 않는다. 업로드 성공 후 실제 절대경로가 입력창에 추가되므로 모델이 `file_read`, `document_read`, `image_analyze` 등을 바로 사용할 수 있다.

같은 계정의 전용 폴더 안에서 같은 이름의 파일은 조용히 덮어쓰지 않고 HTTP 409로 실패한다. path separator를 포함한 filename도 거부한다. 서로 다른 trial 계정은 같은 filename을 각자의 폴더에 독립적으로 올릴 수 있다.

기존 버전에서 `./mai_uploads/` 바로 아래에 저장했던 legacy trial upload는 소유자 정보가 남아 있지 않으므로 자동 migration 또는 자동 삭제하지 않는다.

---

# 8. Trial 계정 초기화 / 재사용

사용이 끝난 trial ID를 다른 사람에게 다시 줄 때 새 ID를 만들 필요 없이 `reset_trial.py`로 해당 계정을 초기 상태에 가깝게 되돌릴 수 있다.

**반드시 MAI 서버를 먼저 종료한 상태에서 실행한다.** 실행 중에는 memory concept index가 process memory에도 올라가 있으므로 DB만 외부에서 변경하면 runtime cache와 불일치할 수 있다. 스크립트 자체도 설정된 MAI host/port가 열려 있으면 초기화를 거부한다.

먼저 삭제 대상을 확인하려면:

```bash
python reset_trial.py trial-a --dry-run
```

실제 초기화:

```bash
python reset_trial.py trial-a
```

실행하면 대상 trial ID와 삭제 예정 memory/upload 정보를 보여준 뒤, 같은 trial ID를 다시 입력해야 삭제가 진행된다.

자동 확인이 필요한 경우:

```bash
python reset_trial.py trial-a --yes
```

기본 경로가 아닌 DB 또는 upload root를 쓰는 경우 직접 지정할 수도 있다.

```bash
python reset_trial.py trial-a \
  --db ./data/memory.sqlite3 \
  --upload-root ./mai_uploads
```

Windows PowerShell에서는 한 줄로 실행하거나 PowerShell 문법에 맞게 줄을 나눈다.

초기화 대상:

- 해당 trial의 User Anchor
- 해당 trial 소유 Utterance / Fact node
- 해당 Utterance가 참조하는 evidence
- 해당 trial node 삭제 후 아무 사용자에게도 연결되지 않는 orphan Concept와 Concept index row
- 해당 trial의 전용 upload directory 전체
- 서버 재시작으로 사라지는 process-local login/chat session

보존 대상:

- Owner memory
- 다른 trial의 memory
- 다른 사용자가 아직 공유하고 있는 Concept node/index
- 다른 trial의 upload directory
- owner upload
- 소유권을 판별할 수 없는 구버전 `./mai_uploads/` root의 legacy file

안전장치:

- `.env`의 `TRIAL_IDS`에 실제 등록된 ID만 허용
- owner ID는 reset 대상으로 거부
- MAI 서버가 실행 중이면 거부
- 기본 실행은 trial ID 재입력 확인 필요
- `--dry-run` 지원

초기화가 끝난 뒤 다시 `python run_server.py`를 실행하고 해당 trial ID를 새 사용자에게 전달한다.

---

# 9. Tailscale Funnel 공개

MAI는 필요할 경우 **Tailscale Funnel로 Web UI를 public internet에 공개**한다.

```env
TAILSCALE_FUNNEL=true
```

실행 시 MAI는 다음과 같은 Funnel 설정을 적용하고 `tailscale funnel status` 결과를 출력한다.

```bash
tailscale funnel --bg --yes 8000
```

```text
MAI local: http://127.0.0.1:8000
MAI Tailscale Funnel (public internet):
Available on the internet:
https://<device>.<tailnet>.ts.net
```

과거 `TAILSCALE_SERVE=true` 설정은 폐기됐다. 해당 값이 남아 있으면 잘못된 tailnet-only 모드로 조용히 동작하지 않고 startup에서 실패한다.

Funnel은 인터넷에 공개되므로 Web UI의 ID access control과 trial 권한 분리가 실제 보안 경계다.

---

# 10. 실패 처리 원칙

MAI는 실패를 성공처럼 감추지 않는 것을 runtime 계약으로 둔다.

명시적 실패 예시는 다음과 같다.

```text
unknown tool
invalid tool schema
Pydantic validation error
file not found
permission denied
unsupported document/image format
terminal non-zero return code
terminal timeout
web/network failure
invalid public URL target
SQLite / FTS5 failure
memory identity conflict
unauthorized model selection
Tailscale Funnel failure
```

필수 계약을 문자열 비교, 임시 우회, fallback 남용으로 덮지 않는다.

---

# 11. 설치와 실행

Python 가상환경에서:

```bash
python -m pip install -e ".[dev]"
```

`.env.example`을 `.env`로 복사하고 최소한 owner identity와 memory identity를 설정한다.

```env
MAIN_MODEL=ornith-1.5:9b
VISION_MODEL=
OWNER_ID=my-owner-login
OWNER_MEMORY_ID=local-user
TRIAL_IDS=trial-a,trial-b
MEMORY_DB_PATH=./data/memory.sqlite3
SENTENCE_BREAKER_DB_PATH=./data/sentence_breaker.sqlite3
MAI_UPLOAD_ROOT=./mai_uploads
MAI_HOST=127.0.0.1
MAI_PORT=8000
TAILSCALE_FUNNEL=false
```

실행:

```bash
python run_server.py
```

전체 테스트:

```bash
python -m pytest -v
```

---

# 12. 프로젝트 핵심

MAI의 핵심을 한 문장으로 줄이면 다음과 같다.

> **사용자가 직접 소유하는 관계형 장기기억을, 교체 가능한 로컬 LLM의 대화와 실제 PC 작업에 연결하는 개인 에이전트 런타임.**
