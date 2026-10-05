# MAI MyAI sLLM

MAI는 **사용자를 장기적으로 기억하고, 그 기억을 바탕으로 대화·검색·문서 이해·로컬 PC 작업까지 이어 가는 로컬 sLLM 개인 에이전트 런타임**이다.

장기기억은 모델 자체에 맡기지 않고 로컬 SQLite graph에 저장한다. 메인 모델을 바꾸더라도 memory DB는 유지되며, 모델은 필요할 때 native tool을 통해 기억과 외부 정보를 조회한다.

세부 문서:

- [`MEMORY_V1.md`](MEMORY_V1.md): memory graph와 retrieval 구조
- [`WORKING_CONTRACT.md`](WORKING_CONTRACT.md): 현재 runtime 구현 계약
- [`RUN_UI.md`](RUN_UI.md): Web UI, 계정, Tailscale 실행

---

## 1. 현재 production 흐름

현재 production은 pure-agent C 계열의 multi-round native-tool agent다.

```text
User
  ↓
Web/API authentication
  ↓
AccessPrincipal(user_id, db_id, role)
  ↓
Main Agent + Ollama native tool calls
  ↓
Candidate Final
  ↓
FinalGroundingVerifier
  ├─ numeric grounding
  ├─ claim / evidence grounding
  ├─ scope preservation
  ├─ temporal consistency
  ├─ action outcome verification
  ├─ task alignment
  └─ evidence coverage
  ↓
Final Response
  ↓
Background memory extraction / admission
```

### Native tool selection + evidence verification

Main agent가 전체 native tool schema를 보고 필요한 tool을 직접 선택한다. 사전 tool 필요성 판정, frozen requirements, 필수 tool 실행 여부 gate는 제거했다. Verifier는 호출 횟수나 특정 tool 사용 여부가 아니라 답변의 각 material claim을 실제 user/tool evidence와 비교한다. 근거가 부족하면 main agent가 추가 근거를 얻거나 주장을 축소하고 한계를 명시한다.

Reviewer에는 현재 요청, 대화, 모든 tool 결과, candidate 전체를 전달한다. 과거 assistant 발화나 모델 지식은 사실 근거로 취급하지 않는다. 검증 실패와 correction budget 소진은 오류로 드러난다.

---

## 2. Final verification

Final verifier는 tool을 선택하거나 답을 다시 쓰는 주체가 아니다. Candidate final을 release하기 전에 user/tool evidence와 비교해 검증한다. Numeric grounding은 deterministic 검사이고, semantic review는 현재 chat에 선택된 동일 모델을 `think=False`, `tools=()`로 한 번 더 호출한다. Candidate가 거절되어 재작성되면 새 candidate마다 reviewer가 다시 호출될 수 있다.

현재 검증 축은 다음과 같다.

- **Numeric grounding**: material numeric value가 user/tool evidence에 존재하는지 deterministic 검사
- **Claim grounding**: factual claim이 evidence에 실제로 지지되는지 model review
- **Scope preservation**: 한 파일/한 화면/로컬 상태 근거를 전체/원격/전역 상태로 확장하지 않는지 검사
- **Temporal consistency**: candidate의 시간 표현이 현재 시점 및 evidence의 날짜/타임스탬프와 모순되지 않는지 검사
- **Action outcome**: mutation tool 호출 성공만으로 더 넓은 최종 상태 완료를 주장하지 않는지 검사
- **Task alignment**: 사용자의 실제 요청 대신 다른 작업이나 일반론으로 빠지지 않는지 검사
- **Evidence coverage**: 이미 확보된 유용한 evidence를 버리고 지나치게 빈약하거나 일반적인 답으로 후퇴하지 않는지 검사

Coverage는 “더 검색하면 더 있을 수 있다”를 이유로 부족 판정을 내리지 않는다. **현재 user/tool evidence 안에 이미 있는 구체적이고 사용자에게 중요한 정보를 candidate가 불필요하게 버린 경우**만 대상으로 한다.

각 검증 축의 correction budget은 최대 2회다. 숫자 결함이 있어도 근거·정합성·coverage review를 함께 실행한다. 재시도 후에도 해당 축의 결함이 남으면 VerificationRetriesExhausted로 실패하며, 검증을 생략해서 candidate를 반환하지 않는다.

Semantic reviewer의 structured output이 깨지거나 timeout/failure가 발생하면 실행 실패로 전달한다. 검증되지 않은 candidate는 반환하지 않으며 문자열 heuristic으로 reviewer 출력을 복원하지 않는다.

---

## 3. Failure handling / structural guards

개별 tool의 validation error, unknown tool, handler exception, timeout은 `ok=false`, `error_type`을 포함한 실패 결과로 모델에 반환한다. 같은 model turn의 다른 tool call도 계속 처리하며, 성공과 실패 결과를 함께 다음 turn에 전달한다.

Guard가 차단한 개별 호출도 실패한 `ToolExecution`으로 반환한다. 동일 호출·동일 실패 결과가 3회 연속이면 경고하고, 5회 관찰한 뒤 다음 unchanged 호출을 차단한다. 호출이나 결과가 달라지면 구조적 진행으로 취급한다.

전체 model round 수, 성공 tool call 수, 동일 호출 자체에는 고정 횟수 상한이 없다. 동일한 tool-round 결과가 반복되어 no-progress guard가 걸리면 모델에 구조적 notice를 전달해 접근 변경 또는 실패 보고를 요청한다. 이 notice 자체가 run을 종료하지는 않으므로 전체 loop의 종료를 보장하는 hard ceiling은 아니다.

Main model/runtime의 실제 fatal failure는 별도 답변 생성으로 숨기지 않는다. Agent loop 내부 실패는 확보된 실행 내역을 가진 `AgentRunFailure`로 전달되고, Web/API는 실패 응답을 반환한다. Preflight 등 loop 밖 실패도 HTTP/job 실패 경로로 전달한다. `FailureAnswerFinalizer`는 제거됐다.

Final reviewer 실패와 background memory 실패 로깅은 별도 정책이다. 개별 tool 실패를 최종 답변에서 성공으로 바꾸어 설명해서는 안 된다.

---

## 4. Graph Long-term Memory

MAI memory의 기본 구조는 다음과 같다.

```text
User Anchor
   ├─spoke────────→ Utterance
   └─asserted_fact→ Fact

Utterance ─derived_fact→ Fact
Utterance ─mentions────→ Concept
Fact      ─mentions────→ Concept
```

핵심 node:

- **User Anchor**: `db_id`마다 하나씩 존재하는 사용자 기준점
- **Utterance**: 원문 사용자 evidence
- **Fact**: 발화에서 파생된 durable fact
- **Concept**: Sentence_Breaker canonical segment로 정의되는 재사용 가능한 개념

원문 Utterance와 파생 Fact는 분리해 보존한다.

Retrieval은 embedding/vector space를 production identity로 사용하지 않는다.

```text
query
  ↓ Sentence_Breaker
canonical segments
  ↓
exact hash lookup
  ↓ miss
SQLite FTS5 lexical retrieval
  ↓
Concept Nodes
  ↓
Graph neighborhood
```

현재 model-visible memory tool:

- `memory_overview(limit)`
- `memory_recall(query)`
- `memory_search(node_id)`

`memory_search`는 one-hop 확장이다. 더 깊은 탐색은 모델이 추가 tool call로 수행한다.

현재 production은 각 요청에서 빈 `WorkingGraph`로 시작하고 model이 memory tool을 호출해 기억을 가져온다. `auto_recall` 함수는 구현돼 있지만 요청 시작 경로에는 연결돼 있지 않다.

### Post-response memory write

최종 답변 이후 background task에서 같은 turn의 선택 모델을 `think=False` fact extractor로 사용한다. 별도 `MEMORY_MODEL`은 없다.

Recall-only turn에서 extraction이 성공했고 새 fact가 없다면 persistent write를 생략한다. Extraction이 실패하면 실패를 숨기지 않되 raw user turn을 보존하는 방향으로 admission한다.

---

## 5. Native tools

MAI는 Ollama native `tools` / `tool_calls`를 직접 사용한다. Tool routing을 `if text contains ...` 식 문자열 규칙으로 대체하지 않는다.

| 범주 | 주요 도구 |
|---|---|
| Memory | `memory_overview`, `memory_recall`, `memory_search` |
| Time | `current_time` |
| Calculation | `calculator` |
| Files / Documents | `file_list`, `file_search`, `file_read`, mutation tools |
| Code | `code_search`, `code_read`, `code_symbols` |
| Image | `image_analyze` |
| Web | `web_search`, `web_fetch` |
| Market | `market_data` |
| Terminal | `terminal_run` |
| Tool result paging | `tool_result_read` |

`file_read`는 일반 텍스트와 PDF, DOCX, XLSX, CSV, PPTX를 하나의 model-facing read interface로 처리한다. 구조화 문서 형식은 내부 문서 파서로 전달되며, CSV는 기본 UTF-8 BOM 호환 인코딩을 사용하고 필요하면 `cp949` 같은 인코딩을 명시할 수 있다. 모델이 `file_read`와 별도의 문서 읽기 도구 사이에서 route를 선택하게 하지 않는다.

큰 tool result는 bounded page와 `result_id`로 축약될 수 있으며, 모델은 `tool_result_read`로 필요한 범위를 이어 읽는다.

Material arithmetic은 main model 암산보다 `calculator`를 사용하도록 system contract에 명시되어 있다.

`VISION_MODEL`이 비어 있으면 `image_analyze`는 아예 registry에 등록되지 않는다.

---

## 6. 계정: user_id / user_pw / db_id

Owner와 Trial 모두 `.env`에서 `user_info` record로 정의한다.

```env
OWNER_USERS=[{"user_id":"owner","user_pw":"change-me","db_id":"local-user"}]
TRIAL_USERS=[{"user_id":"체험판","user_pw":"0000","db_id":"trial-default"}]
```

세 필드의 의미:

```text
user_id
  로그인 ID
  나중에 변경 가능

user_pw
  로그인 비밀번호
  현재 설계에서는 local .env에 평문으로 저장

db_id
  persistent data의 stable identity
  memory / Web chat / trial upload ownership 기준
```

따라서 로그인 ID를 바꿔도 `db_id`를 유지하면 기존 memory와 chat을 migration 없이 계속 사용할 수 있다.

모든 `user_id`와 `db_id`는 계정 간 충돌하지 않아야 하며, 교차 충돌도 startup에서 거부한다.

기존 `OWNER_ID`, `OWNER_MEMORY_ID`, `OWNER_ACCOUNTS`, `TRIAL_IDS`는 silent fallback으로 사용하지 않는다.

---

## 7. 로그인 세션과 persistent chat

성공 로그인 시 서버가 Bearer token을 발급한다. 같은 `user_id`로 새 로그인하면 이전 token을 폐기하는 **new-login-wins** 정책이다.

브라우저는 마지막 성공 로그인한 `user_id`만 localStorage에 기억한다. 비밀번호는 저장하지 않는다. 따라서 다른 기기 로그인으로 기존 세션이 끊겨도 원래 브라우저에는 ID가 남아 있어 비밀번호만 다시 입력하면 된다.

대화 기록은 `db_id` 기준으로 `CHAT_DB_PATH`의 `web_chat_messages` 테이블에 저장한다. 저장된 전체 대화와 조회 window는 분리되어 있다. 현재 `SESSION_HISTORY_MESSAGES`는 모델에 전달하는 최근 user/assistant message 수와 Web UI에서 복원하는 대화 window에 함께 적용된다. 기본 실행 `python run_server.py`와 `.env.example`은 12개(일반적인 교대 대화 약 6쌍)이며, 직접 server 경로를 사용할 때 환경 설정이 없으면 24개다. `새 채팅`은 실행 중 job을 취소하고 현재 persisted chat session을 지우며 장기기억은 유지한다.

브라우저/폰이 닫혀도 서버 process가 살아 있는 동안 running chat job은 계속될 수 있고, 완료된 assistant answer는 persistent chat에 저장된다. 단, running job 자체는 외부 queue가 아니라 process memory에 있으므로 서버 process restart를 넘겨 이어 실행되지는 않는다.

---

## 8. Owner / Trial 권한

### Owner

Owner는 설치된 Ollama 모델 중 선택할 수 있고 전체 local mutation/terminal capability를 사용할 수 있다.

### Trial

Trial model은 `MAIN_MODEL`로 고정된다. Client가 다른 model을 직접 POST해도 서버가 거부한다.

Trial은 read/search 계열과 자기 upload directory 내부의 `file_write` / `file_create`만 허용된다.

```text
Trial 사용 가능
  memory_*
  current_time / calculator
  file_list / file_search / file_read
    └─ text / PDF / DOCX / XLSX / CSV / PPTX
  code_search / code_read / code_symbols
  image_analyze   # configured only
  web_search / web_fetch
  market_data
  file_write / file_create   # own upload directory only

Trial 미노출
  file_delete / file_move / file_copy
  terminal_run
```

Trial upload ownership 역시 `db_id` 기준이다. Trial의 read/search 도구는 OS 계정이 접근할 수 있는 로컬 경로를 읽을 수 있으며, 자기 upload directory로 제한되는 것은 write/create다.

---

## 9. 기본 실행

설치:

```bash
python -m pip install -e ".[dev]"
```

`.env.example`을 `.env`로 복사한 뒤 계정과 모델 설정을 수정한다.

기본 예시 모델:

```env
MAIN_MODEL=ornith-1.5:9b
OLLAMA_REQUEST_TIMEOUT_SECONDS=300
```

`OLLAMA_REQUEST_TIMEOUT_SECONDS`는 Ollama 요청의 transport timeout을 설정한다. Final reviewer와 fact extractor에는 기본 15초 작업 제한이 없다. 명시적으로 timeout을 설정한 경우에만 작업 제한을 적용한다.

`MAI_CWD`가 설정돼 있으면 상대 로컬 경로의 기준으로 사용한다. 비어 있으면 process working directory를 사용하며, OS 사용자 home으로 자동 변경하지 않는다.

실행:

```bash
python run_server.py
```

로컬 UI:

```text
http://127.0.0.1:8000
```

Trial 기본 example:

```text
ID: 체험판
PW: 0000
```

상세 실행과 Tailscale Funnel 설정은 [`RUN_UI.md`](RUN_UI.md)를 참고한다.

---

## 10. 실패 원칙

MAI는 contract violation을 문자열 비교나 임시 fallback으로 성공처럼 숨기지 않는다.

예:

- invalid tool schema / arguments
- unknown tool
- file or permission failure
- trial permission violation
- terminal non-zero / timeout
- web/network failure
- private URL rejection
- SQLite / FTS5 failure
- identity collision
- Tailscale Funnel failure

개별 tool 실패 이후 agent가 정상적으로 final을 생성할 수 있다면 확보된 결과와 실패를 구분한 truthful partial answer를 전달한다. 실제 fatal runtime 실패는 별도 finalizer로 대체하지 않고 오류로 전달한다. Final reviewer 장애와 미해결 검증의 retry budget 소진은 실행 실패로 전달한다.

거절된 초안은 승인 전까지 내부 이력에 보관하지만, 재시도 모델 입력에서는 일반 assistant 대화에서 제외하고 delivered=false인 JSON 검토 자료로 전달한다. 거절된 thinking은 재전송하지 않는다. 최종 승인 후 거절된 초안만 삭제하며 승인된 답변과 실제 이전 대화는 보존한다. 고정된 필수 툴 목록과 누락 상태는 첫 라운드부터 전달한다.

Reviewer 호출의 일시적인 연결/timeout 오류, 429·5xx, 응답 protocol 및 JSON/schema 오류는 최초 호출 이후 최대 2회 재시도한다. 유효한 거절 판정은 재추첨하지 않고 본체 수정으로 보낸다. 설정 오류와 재시도 소진은 실제 오류로 종료한다. 각 검증 입력에는 `current_time` 툴과 동일한 OS 시계 구현으로 읽은 timezone-aware 현재 시각을 제공한다. 현재 시각은 자료의 최신성이나 사용자 timezone을 대신 증명하지 않는다. Ollama 요청 timeout 기본값 및 예시 설정은 300초이며, 기존 `.env`의 명시 값이 있으면 그 값이 우선한다.
