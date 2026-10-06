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
LLM tool-requirement preflight
  ↓
FrozenToolRequirements
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

### Tool preflight + native-tool selection

Production request path는 main agent 전에 선택된 동일 모델을 `think=False`, `tools=()`로 호출해 필수 native tool을 판정한다. 판정 결과는 `FrozenToolRequirements`로 고정되며, 등록된 handler가 실제로 시작돼야 충족된다.

Preflight 입력은 `request_context`와 `factual_evidence`를 분리한다. 이전 assistant 답변은 참조 해석용 context일 뿐 사실 근거가 아니다. 최종 답변에 필요한 material external fact가 factual evidence에 없다면 모델의 학습 지식을 근거로 간주하지 않고 적절한 정보 tool을 필수로 선택한다.

Main agent가 필수 tool 없이 final을 시도하면 아직 누락된 tool schema만 노출하는 correction round로 돌아간다. 그 밖의 추가 tool은 main agent가 전체 native schema를 보고 직접 선택한다.

---

## 2. Final verification

Final verifier는 tool을 선택하거나 답을 다시 쓰는 주체가 아니다. Candidate final을 release하기 전에 user/tool evidence와 비교해 검증한다. Numeric grounding은 deterministic 검사이고, semantic review는 현재 chat에 선택된 동일 모델을 `think=False`, `tools=()`로 한 번 더 호출한다. Candidate가 거절되어 재작성되면 새 candidate마다 reviewer가 다시 호출될 수 있다.

Semantic reviewer 입력도 `request_context`와 ID가 부여된 `factual_evidence`로 분리한다. 이전 assistant 답변은 요청 해석과 alignment 판단에만 사용하며 claim grounding, coverage, action outcome의 근거가 될 수 없다. 각 supported claim은 실제 factual evidence의 `evidence_ids`를 반환해야 하고, coverage 부족 판정도 `coverage_evidence_ids`를 제시해야 한다. ID 누락·중복·미등록 참조는 reviewer protocol failure로 처리한다.

현재 검증 축은 다음과 같다.

- **Numeric grounding**: material numeric value가 user/tool evidence에 존재하는지 deterministic 검사
- **Claim grounding**: factual claim이 evidence에 실제로 지지되는지 model review
- **Scope preservation**: 한 파일/한 화면/로컬 상태 근거를 전체/원격/전역 상태로 확장하지 않는지 검사
- **Temporal consistency**: candidate의 시간 표현이 현재 시점 및 evidence의 날짜/타임스탬프와 모순되지 않는지 검사
- **Action outcome**: mutation tool 호출 성공만으로 더 넓은 최종 상태 완료를 주장하지 않는지 검사
- **Task alignment**: 사용자의 실제 요청 대신 다른 작업이나 일반론으로 빠지지 않는지 검사
- **Evidence coverage**: 이미 확보된 유용한 evidence를 버리고 지나치게 빈약하거나 일반적인 답으로 후퇴하지 않는지 검사

Coverage는 “더 검색하면 더 있을 수 있다”를 이유로 부족 판정을 내리지 않는다. **현재 user/tool evidence 안에 이미 있는 구체적이고 사용자에게 중요한 정보를 candidate가 불필요하게 버린 경우**만 대상으로 한다.

Candidate가 grounding에서 반려되면 correction round에는 blocked claim, defect, reason, 기존 evidence ID와 허용된 해결 방식이 구조화된 JSON으로 전달된다. 새 user/tool evidence를 확보하거나, 주장을 제거하거나, 미확인 상태를 명시해야 하며 같은 근거로 사실 설명만 바꿔 반복할 수 없다.

각 검증 축의 correction budget은 최대 2회다. Semantic reviewer의 structured output, evidence ID 계약, timeout 또는 provider 호출이 재시도 뒤에도 실패하면 검증되지 않은 candidate를 반환하지 않고 실행 실패로 전달한다.

모든 Ollama 호출은 `preflight`, `main`, `correction`, `reviewer`, memory 단계 label과 함께 입력 메시지 수·문자 수·tool schema 크기·wall time을 기록한다. Ollama가 제공하면 prompt/eval token 수와 load/prompt-eval/eval/total duration도 함께 기록한다.

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

MAI memory의 기본 production 구조는 Fact-first다.

```text
User Anchor ─asserted_fact→ Fact ─mentions→ Concept
```

`.env`의 `MEMORY_RECALL_INCLUDE_UTTERANCES=true`일 때만 새 Utterance graph node도 함께 기록한다.

```text
User Anchor ─spoke──────→ Utterance
Utterance   ─derived_fact→ Fact
Utterance   ─mentions────→ Concept
```

핵심 node:

- **User Anchor**: `db_id`마다 하나씩 존재하는 사용자 기준점
- **Fact**: 사용자 발화에서 폭넓게 추출한 durable fact
- **Concept**: Sentence_Breaker canonical segment로 정의되는 재사용 가능한 개념
- **Utterance**: 옵션. 토글이 켜진 경우에만 원문 발화 graph node를 생성

원문 자체는 Utterance node 사용 여부와 별개로 immutable `evidence` table에 보존한다.

Retrieval은 embedding/vector space를 production identity로 사용하지 않는다. 저장 시에는 Sentence_Breaker로 Concept을 만들지만, recall query는 다시 Sentence_Breaker로 분해하지 않는다.

```text
model recall query
  ↓ whitespace chunks
intact query chunks
  ├─ Fact canonical_text 포함검색
  └─ ConceptIndex exact/FTS5 검색
       └─ chunk당 최고 Concept seed 1개
  ↓
bounded Fact + Concept context
```

현재 model-visible memory tool:

- `memory_overview(limit)`
- `memory_recall(query)`
- `memory_search(node_id)`

Recall 시 User Anchor의 전체 `spoke` one-hop을 자동으로 붙이지 않는다. Anchor 기본 context는 `asserted_fact` Fact만 bounded set으로 가져오며, **recency를 우선**하고 `occurrence_count`는 동률 보조로만 사용한다. Query Fact 포함검색도 match relevance → recency → occurrence_count 순으로 정렬한다.

`memory_recall`은 공백 chunk 각각을 그대로 검색 단위로 사용한다. 각 chunk가 포함된 Fact 본문을 직접 찾고, 동시에 ConceptIndex에서 chunk당 최고 Concept seed 하나를 선택해 연결된 Fact context를 더한다. 따라서 `"만년필"`을 검색하면 하나의 Concept node만 보여주는 것이 아니라 본문에 `"만년필"`이 포함된 여러 Fact도 bounded result로 함께 들어온다.

`MEMORY_RECALL_INCLUDE_UTTERANCES=false`가 기본이며 이 경우 **새 Utterance graph node를 만들지 않고 recall에도 Utterance를 넣지 않는다.** `true`로 바꾸면 두 동작을 함께 활성화한다. raw user evidence는 토글과 무관하게 별도 evidence table에 보존한다.

Working Graph 자체는 한 turn 안에서 누적되지만 `memory_recall`과 `memory_search`의 tool result는 매 호출에서 새로 조회·확장된 payload만 반환한다. 따라서 여러 번 조회해도 이미 본 전체 Working Graph를 매번 모델 context에 재전송하지 않는다.

`memory_search`는 일반 node에 대해 one-hop 확장이다. User Anchor를 직접 확장할 때는 unbounded `spoke` traversal 대신 동일한 bounded Fact context를 반환한다. 더 깊은 탐색은 모델이 추가 tool call로 수행한다.

### Post-response memory write

최종 답변 이후 background task에서 같은 turn의 선택 모델을 `think=False` fact extractor로 사용한다. 별도 `MEMORY_MODEL`은 없다. 모델에게 별도 `memory_write` tool이 노출되지 않아도 이 background admission이 자동으로 실행된다.

Fact extractor는 최소 요약 하나만 남기기보다, 이후 recall에 도움이 될 수 있는 사용자 상태·소유물·구성·변경·선호·이유·호환성 같은 세부사항을 여러 개의 self-contained Fact로 폭넓게 추출하도록 한다. 고정 Fact 개수 상한은 두지 않는다.

Fact node identity는 #199 이전 방식으로 유지한다. 같은 사용자에서 canonical Fact text가 완전히 같으면 기존 node를 재사용하고 `occurrence_count`를 올린다. Concept도 동일 canonical segment면 기존 node를 재사용한다. 반면 text가 다른 Fact를 LLM이 의미상 같다고 판단해 기존 node에 합치는 semantic identity merge는 사용하지 않는다.

동일 `(from_node_id, to_node_id, relation)` edge가 다시 관찰되면 기존 edge를 그대로 재사용하고 중복 row를 만들지 않는다. Edge 자체에는 별도 `occurrence_count` 강화/약화 가중치를 두지 않는다.

Recall-only turn에서 extraction이 성공했고 새 fact가 없다면 persistent write를 생략한다. Extraction이 실패하면 실패를 숨기지 않고 raw evidence를 보존한다.

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

큰 tool result는 bounded page와 `result_id`로 축약될 수 있으며, 모델은 `tool_result_read`로 필요한 범위를 이어 읽는다. 다만 `web_search`는 모델이 검색 결과 pagination을 따로 따라가지 않도록 provider의 3개 result page를 한 번에 조회해 최대 15개를 기본 반환하고, title/URL/짧은 snippet만 유지한다. 실제 페이지 본문은 `web_fetch`가 담당한다.

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
MEMORY_RECALL_INCLUDE_UTTERANCES=false
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
