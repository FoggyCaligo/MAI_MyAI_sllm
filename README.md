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

### Direct native-tool selection

Production request path는 별도의 model-based tool requirement preflight를 호출하지 않는다. Main agent가 system prompt, 최근 대화, runtime context, 현재 등록된 native tool schema를 함께 보고 필요한 tool을 직접 선택한다.

`OllamaToolRequirementPlanner`와 `FrozenToolRequirements` 지원 코드는 구조적 실험 및 단위 테스트를 위해 남아 있지만 현재 production composition에는 연결하지 않는다. 추가 LLM 호출의 지연을 피하기 위한 의도적인 선택이다.

따라서 production에서는 required-tool gate가 final을 구조적으로 차단하지 않는다. 필요한 tool 사용은 main agent prompt와 tool schema에 의존하고, 생성된 candidate final의 근거성은 뒤의 `FinalGroundingVerifier`가 검토한다.

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

Coverage correction은 별도 budget으로 최대 2번이다. 두 번 이후에는 coverage 부족만으로 final을 계속 붙잡지 않는다. Grounding, action, alignment와는 별도 축이다.

Semantic reviewer의 structured output이 깨지거나 timeout/failure가 발생하면 이를 log하고 **fail-open**한다. Reviewer 장애 때문에 전체 사용자 요청을 서비스 오류로 끝내기보다 candidate final을 반환하는 가용성 우선 정책이다. 실패한 reviewer 출력을 문자열 heuristic으로 복원하지 않는다.

---

## 3. Failure recovery

Main planner/agent 실행이 fatal exception으로 끝나더라도 확보된 tool evidence가 있다면 `FailureAnswerFinalizer`가 **tool을 추가 호출하지 않고** 사용자에게 보여줄 수 있는 마지막 답변을 한 번 생성한다.

Recovery final은 다음을 지켜야 한다.

- 실제 실패를 숨기지 않는다.
- 성공하지 않은 작업을 성공했다고 주장하지 않는다.
- 확보된 결과와 실패한 부분을 구분한다.
- 확인된 사실, 실패, 미확인 상태를 구분한다.
- 가능한 경우 유용한 partial answer를 반환한다.

Recovery finalization 자체도 실패하면 원래 exception을 다시 드러낸다.

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
| Files | `file_list`, `file_search`, `file_read`, mutation tools |
| Code | `code_search`, `code_read`, `code_symbols` |
| Document | `document_read` |
| Image | `image_analyze` |
| Web | `web_search`, `web_fetch` |
| Market | `market_data` |
| Terminal | `terminal_run` |
| Tool result paging | `tool_result_read` |

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

대화 기록은 `db_id` 기준으로 `CHAT_DB_PATH`의 `web_chat_messages` 테이블에 저장한다. 전체 UI history와 모델 context는 분리되어 있으며, 모델에는 최근 `SESSION_HISTORY_MESSAGES`개만 전달할 수 있다.

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
  code_search / code_read / code_symbols
  document_read
  image_analyze   # configured only
  web_search / web_fetch
  market_data
  file_write / file_create   # own upload directory only

Trial 미노출
  file_delete / file_move / file_copy
  terminal_run
```

Trial upload ownership 역시 `db_id` 기준이다.

---

## 9. 기본 실행

설치:

```bash
python -m pip install -e ".[dev]"
```

`.env.example`을 `.env`로 복사한 뒤 계정과 모델 설정을 수정한다.

기본 예시 모델:

```env
MAIN_MODEL=gemma4:e4b
```

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

실패했을 때는 실패로 드러내되, 이미 확보된 유용한 결과가 있다면 사용자에게 truthful partial answer로 전달하는 것을 우선한다.
