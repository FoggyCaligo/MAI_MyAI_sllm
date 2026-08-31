# MAI MyAI sLLM — Working Contract

이 문서는 현재 MAI production runtime이 지켜야 하는 **구조적 구현 계약**을 기록한다. 소개는 [`README.md`](README.md), memory 세부 구조는 [`MEMORY_V1.md`](MEMORY_V1.md), Web UI 실행은 [`RUN_UI.md`](RUN_UI.md)를 기준으로 한다.

---

## 1. Production path

현재 production request path는 다음 순서를 따른다.

```text
Authenticated Principal
  ↓
Model-based Tool Requirement Preflight
  ↓
FrozenToolRequirements
  ↓
AgentRuntime / AgentLoop
  ↓ native tool calls
ToolRegistry
  ↓
Candidate Final
  ↓
FinalGroundingVerifier
  ↓
Final Response
  ↓
Background Memory Post-processing
```

Framework는 사람/정체성/주제/관계/tool 필요 여부/correction 의도를 `if text contains ...` 같은 문자열 heuristic으로 판정하지 않는다.

---

## 2. Tool requirement preflight

Main agent 실행 전에 `OllamaToolRequirementPlanner`가 같은 선택 모델을 사용해 required native tool 집합을 구조화된 output으로 결정한다.

Planner contract:

- `think=False`
- `tools=()`
- strict structured output
- 결과는 exact registered tool name 목록
- unknown tool을 선택하면 명시적 실패
- optional detail만을 위해 tool을 강제하지 않음
- 현재 시점과 비교가 필요한 요청은 현재 시간이 이미 확립된 경우가 아니면 available current-time tool을 요구할 수 있음

Planner가 만든 `FrozenToolRequirements`는 해당 run 동안 변하지 않는다.

Agent가 required tool 실행 결과 없이 final을 시도하면 final을 거부한다. Correction round에서는 아직 missing인 required tool schema만 노출하고, required tool이 handler execution result를 만들 때까지 requirement를 해제하지 않는다.

---

## 3. Native tool contract

MAI는 Ollama native `tools` / `tool_calls`를 직접 사용한다.

`OllamaAdapter`는 protocol translation만 담당한다. Tool call을 임의 텍스트 JSON으로 복원하거나 `<think>` 문자열을 파싱해 의미를 추정하지 않는다.

Tool schema/protocol 위반은 자동 보정하지 않고 명확하게 실패한다.

---

## 4. ToolRegistry

모든 model-visible capability는 `ToolRegistry`의 `ToolDefinition`으로 등록한다.

```text
name
description
Pydantic input schema
handler
timeout
category
```

Registry는 tool 이름의 의미를 문자열로 해석하지 않는다. Validation error, unknown tool, handler exception, timeout은 구조화된 실패로 반환한다.

큰 tool output은 `ToolResultStore`가 bounded model view로 축약할 수 있다. 원본의 추가 범위가 필요하면 model은 `tool_result_read(result_id, offset, limit)`를 사용한다.

---

## 5. Agent loop / guards

AgentLoop는 multi-round model/tool 실행을 담당한다.

구조적 guard는 identical call, identical failure, no-progress repetition 등을 제한한다. 현재 production contract에 별도의 global maximum model-round ceiling을 전제로 문서화하지 않는다.

Guard는 semantic router가 아니다. Tool 실패 문자열을 보고 의미를 추측해 다른 route로 바꾸지 않는다.

---

## 6. Final verification

`FinalGroundingVerifier`는 candidate final을 release 전에 검증한다. Verifier는 tool을 호출하거나 답안을 재작성하지 않는다.

### 6.1 Numeric grounding

Material numeric fact가 current user messages 또는 tool evidence에 존재하는지 deterministic 검사한다.

### 6.2 Model semantic review

Reviewer는 strict structured output으로 다음 축을 반환한다.

```text
evidence_verdict
alignment_verdict
coverage_verdict
coverage_reasons
claims[]
action_verdict
reasons
```

Claim defect는 현재 다음 구조를 사용한다.

```text
none
scope_expansion
contradiction
unsupported_inference
missing_evidence
```

### 6.3 Temporal consistency

각 material claim의 시간적 framing이 current date/time 및 supplied evidence의 날짜/타임스탬프와 일관되는지 reviewer가 의미적으로 판단한다.

행사/주식/뉴스 같은 특정 도메인 문자열 규칙으로 시간 판정을 구현하지 않는다.

### 6.4 Evidence coverage

Coverage는 “더 많은 정보를 찾아올 수 있었는가”가 아니다.

다음 경우에만 `insufficient`가 될 수 있다.

- user/tool evidence에 이미 구체적 사실이 있음
- 그 사실이 현재 사용자 요청에 material하게 유용함
- candidate가 이를 생략해 지나치게 generic/evasive/thin answer로 후퇴함

다음은 coverage failure가 아니다.

- 추가 검색 가능성
- exhaustive listing 미제공
- optional background 생략
- evidence 밖의 추정 미제공

Coverage correction budget은 grounding/semantic budget과 별도이며 **최대 2회**다. Budget 소진 후에는 coverage 부족만으로 더 block하지 않는다.

### 6.5 Action outcome

Mutation tool invocation의 success는 그 tool contract가 성공했다는 evidence다. 더 넓은 requested end state까지 완료됐다고 주장하려면 같은 scope의 authoritative result 또는 resulting-state evidence가 필요하다.

### 6.6 Reviewer failure

Reviewer timeout, structured-output schema violation, reviewer exception은 명시적으로 log하고 semantic 내용을 문자열 fallback으로 복원하지 않는다.

---

## 7. Failure recovery finalization

Main planner/agent 실행이 fatal exception으로 종료되면 production composition root는 `FailureAnswerFinalizer`를 한 번 호출할 수 있다.

Recovery contract:

- no tools
- `think=False`
- 실제 failure를 숨기지 않음
- tool evidence 없이 성공을 발명하지 않음
- confirmed / failed / unknown을 구분
- 가능한 supported partial result를 사용자에게 반환

Recovery finalizer 자체가 실패하면 원래 exception을 다시 raise한다.

---

## 8. Memory identity / account identity

현재 `AccessPrincipal`의 의미적 contract는 다음과 같다.

```text
user_id   = 로그인 identity
memory/db identity = db_id
role      = OWNER | TRIAL
```

구현에는 이전 naming과의 compatibility property가 남아 있을 수 있지만, persistent data contract는 `db_id`를 기준으로 이해한다.

`.env` account record는 정확히 다음 세 string field를 사용한다.

```json
{"user_id":"owner","user_pw":"change-me","db_id":"local-user"}
```

`user_id`는 바꿀 수 있지만, 기존 memory/chat/upload를 이어 쓰려면 `db_id`는 유지한다.

`user_id`, `db_id`는 계정 간 고유해야 하며 서로 교차 충돌하는 설정도 startup에서 거부한다.

Legacy `OWNER_ID`, `OWNER_MEMORY_ID`, `OWNER_ACCOUNTS`, `TRIAL_IDS`를 silent fallback으로 사용하지 않는다.

---

## 9. Login session contract

Owner와 Trial 모두 ID + password 로그인이다.

- password는 현재 local `.env`에 plaintext로 저장한다.
- 로그인 실패는 ID 오류와 password 오류를 구분해 노출하지 않는다.
- 성공 시 Bearer token 발급
- 같은 `user_id`의 새 로그인은 기존 token을 폐기함
- 다른 계정 token에는 영향 없음

브라우저는 마지막 성공 `user_id`만 localStorage에 저장한다. Password는 browser storage에 저장하지 않는다.

---

## 10. Persistent chat contract

Web UI persistent conversation ownership은 `db_id` 기준이다.

`CHAT_DB_PATH` 안에서 Web UI는 전용 `web_chat_messages` 테이블을 사용한다.

Startup migration은 이미 알려진 legacy persistent-chat schema만 구조적으로 이관한다. 같은 DB 안의 unrelated `chat_messages`는 임의 변형하거나 삭제하지 않는다. `web_chat_messages` 자체가 알 수 없는 schema면 startup에서 실패한다.

UI 전체 history와 model context는 별개다. Model에는 최근 `SESSION_HISTORY_MESSAGES`개만 전달할 수 있다.

Running resumable chat job은 process memory에 있다. Browser/device disconnect를 넘겨 실행될 수 있지만 server process restart를 넘겨 계산 자체가 이어지지는 않는다. 완료된 assistant message는 persistent chat에 저장된다.

---

## 11. Permanent memory graph

Permanent memory는 chat history나 vector store 자체가 아니다.

```text
User Anchor
Utterance
Fact
Concept
```

Relations:

```text
user_anchor -> utterance : spoke
user_anchor -> fact      : asserted_fact
utterance   -> fact      : derived_fact
utterance   -> concept   : mentions
fact        -> concept   : mentions
```

Utterance는 source evidence로 보존하고 Fact가 원문을 덮어쓰지 않는다.

Concept identity는 Sentence_Breaker canonical segment가 결정한다. FTS 결과가 Concept identity를 합치지 않는다.

Persistent retrieval:

```text
query
  ↓ Sentence_Breaker
canonical segments
  ↓
exact hash lookup
  ↓ miss
SQLite FTS5 lexical retrieval
  ↓
Concept Node
```

Legacy vector/sqlite-vec table이 DB에 남아 있어도 자동 삭제하지 않는다.

---

## 12. Memory tool contract

Model-visible memory entry:

```text
memory_overview(limit)
memory_recall(query)
memory_search(node_id)
```

`memory_search`는 one-hop 확장이다. Helper가 숨겨진 arbitrary-depth traversal을 수행하지 않는다.

---

## 13. Post-response memory write

Long-term graph mutation은 main agent/tool loop 뒤의 background lifecycle이다.

선택된 chat model과 동일한 모델을 `think=False` `OllamaFactExtractor`로 사용한다. 별도 `MEMORY_MODEL`은 없다.

Fact extraction input은 raw user turn, final answer context, successful non-recall tool evidence를 사용한다. `memory_overview`, `memory_recall`, `memory_search`로 읽어온 과거 memory는 새 world fact evidence처럼 재저장하지 않는다.

Recall-only + extraction success + new fact 없음이면 persistent write를 skip할 수 있다.

Extraction failure는 log에 드러내고 raw user turn 보존 방향으로 admission한다.

---

## 14. Owner / Trial model contract

Owner는 설치된 Ollama model을 선택할 수 있다.

Trial은 configured `MAIN_MODEL` 하나로 고정한다. UI 제한에 의존하지 않고 server boundary에서 다른 model request를 거부한다.

Trial용 별도 model setting은 두지 않는다.

---

## 15. Filesystem / upload permissions

Owner local tools는 repository confinement를 기본 전제로 하지 않는다. OS 계정이 접근 가능한 절대경로를 정식 입력으로 사용할 수 있다.

Trial은 PC-wide read/discovery는 가능하지만 arbitrary mutation은 불가하다.

```text
Trial read
  file_list / file_search / file_read
  code_search / code_read / code_symbols
  document_read
  image_analyze  # configured only

Trial mutation
  file_write / file_create
  └─ own upload directory boundary only

Trial unavailable
  file_delete / file_move / file_copy
  terminal_run
```

Trial upload ownership/path derivation은 `db_id` 기준이다. Resolved path boundary는 handler에서 강제한다.

---

## 16. Document / image / web / market / time

`document_read` 지원 형식:

```text
PDF DOCX XLSX CSV PPTX
```

문서 read 실패를 generic `file_read` 성공으로 위장하지 않는다.

`image_analyze`는 `VISION_MODEL`이 설정된 경우에만 registry에 등록한다.

`web_fetch`는 public HTTP(S)만 허용하고 loopback/private target 및 redirect destination을 검사한다.

`market_data`는 현재 한국 상장주식 current quote/valuation read-only source를 사용한다. Protocol drift는 failure로 드러낸다.

`current_time()`은 host OS의 local/UTC time과 timezone/offset을 제공한다. Timezone을 문자열 query heuristic으로 추정하지 않는다.

---

## 17. Tailscale Funnel

Remote public access는 `TAILSCALE_FUNNEL=true`로 구성한다.

Legacy `TAILSCALE_SERVE=true`는 retired config이며 조용히 다른 의미로 처리하지 않는다.

Funnel CLI/config/status failure는 startup failure로 드러낸다.

---

## 18. Failure principle

필수 contract violation은 실패로 드러내는 것을 기본으로 한다.

금지:

- 오류 문자열을 비교해 semantic workaround 선택
- 실패를 성공처럼 포장하는 fallback
- helper가 main response contract를 우회
- 특정 주제/관계/identity를 문자열 rule로 판정

허용되는 recovery는 **실패 사실을 유지한 채 이미 확보된 evidence로 truthful partial answer를 만드는 것**이다.
