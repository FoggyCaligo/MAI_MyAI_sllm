# MAI MyAI sLLM — Working Contract

확인 기준: 2026-11-04, `dev`의 `ef4833078349a3799881d2307b5d0e8b00164ee7`.

이 문서는 현재 MAI production runtime이 지켜야 하는 **구조적 구현 계약**을 기록한다. 소개는 [`README.md`](README.md), memory 세부 구조는 [`MEMORY_V1.md`](docs/MEMORY_V1.md), Web UI 실행은 [`setup.md`](setup.md)를 기준으로 한다.

---

## 1. Production path

현재 production request path는 다음 순서를 따른다.

```text
Authenticated Principal
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

## 2. Direct native-tool selection

현재 production composition은 main agent 전에 별도의 tool-requirement planner를 호출하지 않는다. Main agent에는 등록된 도구의 **이름/카테고리 카탈로그**와 `tool_manual`의 native schema만 먼저 노출된다. Main agent가 필요한 툴 이름을 `tool_manual(tools=[...])`로 지정해 활성화하면, **다음 model round부터** 활성화한 툴의 전체 native schema가 노출된다. 한 번 활성화된 툴은 해당 run 동안 유지된다.

이 선택은 별도 preflight LLM 호출을 요구하지 않으면서 초기 native tool schema 비용을 줄이기 위한 production 정책이다. 실제 툴이 필요하면 활성화 라운드가 추가된다.

Tool Requirement Planner와 frozen requirements 기반 필수 도구 강제 경로는 제거되었다. Tool 사용 필요성은 카탈로그 및 main agent prompt가 안내하며, final candidate의 근거성은 release 전 verifier가 별도로 검토한다.

---

## 3. Native tool contract

MAI는 기본 Ollama 또는 명시적으로 선택한 llama.cpp adapter의 native `tools` / `tool_calls`를 사용한다. 자동 provider 전환은 없다. `tool_manual`은 AgentLoop가 제공하는 run-local native tool로 활성화와 관측 원문 범위 선택을 담당한다. 상세 실행 도구 인자는 활성화 이후 native schema로 전달되며, 독자적인 JSON 텍스트 실행 프로토콜을 도입하지 않는다.

`OllamaAdapter`는 protocol translation만 담당한다. Tool call을 임의 텍스트 JSON으로 복원하거나 `<think>` 문자열을 파싱해 의미를 추정하지 않는다.

Tool schema/protocol 위반은 자동 보정하지 않고 명확하게 실패한다.

---

## 4. ToolRegistry

실행 가능한 도구는 `ToolRegistry`의 `ToolDefinition`으로 등록한다. 예외적으로 `tool_manual`은 AgentLoop에서 직접 제공하는 native tool이며, Registry의 도구를 추가하거나 외부 동작을 수행하지 않고 해당 run의 노출 schema 집합과 선택한 원문 발췌를 갱신한다.

```text
name
description
Pydantic input schema
handler
timeout
category
```

Registry와 AgentLoop는 tool 이름의 의미를 문자열로 해석하지 않는다. `tool_manual` 활성화는 카탈로그에서 정확히 일치하는 등록 이름만 허용하며, 원문 선택은 해당 run의 result_id와 문자 범위만 허용한다. 미등록 도구/계약 위반은 명시적 실패로 반환한다. 일반적인 validation error, unknown tool, handler exception, timeout도 구조화된 실패로 반환한다.

큰 tool output은 `ToolResultStore`가 bounded model view로 축약할 수 있다. 원본의 추가 범위가 필요하면 model은 `tool_result_read(result_id, offset, limit)`를 사용한다.

---

## 5. Agent loop / guards

AgentLoop는 multi-round model/tool 실행을 담당한다.

구조적 guard는 identical failure와 no-progress repetition을 제한한다. 동일한 성공 호출의 누적 횟수만으로 중단하지 않는다. `AGENT_MAX_IDENTICAL_CALLS`는 현재 동일 실패 경고 시점을 정하는 입력이며 고정 성공 호출 상한이 아니다. 현재 production contract에 별도의 global maximum model-round ceiling을 전제로 문서화하지 않는다.

Guard는 semantic router가 아니다. Tool 실패 문자열을 보고 의미를 추측해 다른 route로 바꾸지 않는다.

---

## 6. Final verification

`FinalGroundingVerifier`는 candidate final을 release 전에 검증한다. Verifier는 외부 실행 tool을 호출하거나 답안을 재작성하지 않는다. 보존된 원문은 구조화된 read_requests로 재열람할 수 있다.

숫자 token 대조는 Python에서 원본 근거로 수행한다. 불일치 값의 의미적 동등성은 별도 숫자 LLM이 아니라 동일 factual reviewer 요청에서 함께 판정한다. Reviewer는 현재 선택된 동일 model을 `think=False`, `tools=()` 및 strict structured output으로 호출한다. 원문 재열람이나 선택적 품질검증에는 추가 호출이 발생할 수 있다. 새 candidate는 다시 검증한다.

### 6.1 Numeric grounding

Material numeric fact가 current user messages, 성공한 원문 tool evidence 또는 이전 승인 대화에 존재하는지 deterministic 검사한다.
불일치가 있으면 숫자검증 지침과 flagged values를 factual request에 추가하고 numeric_verdict/numeric_reason을 필수로 받는다. Python 예외나 필수 판정 누락을 성공으로 처리하지 않는다.

### 6.1.1 Retained evidence

원본은 ToolResultStore와 web chat의 기존 tool_log 감사 기록에 보존한다. Main model에는 bounded page를 한 번 보여준 뒤, 기존 tool_manual.retain_evidence로 지정한 원문 범위와 원본 참조만 유지한다. 선택/발췌에 별도 LLM이나 문자열 의미 heuristic을 사용하지 않는다. 잘못된 ID/범위/예산 위반은 명시적 실패이며 선택과 활성화에 부분 반영하지 않는다.

Verifier는 같은 발췌를 기본 입력으로 받고 필요하면 read_requests로 성공한 원래 source_id의 범위를 다시 읽는다. 재열람은 새 관측이 아니며 외부 tool을 재실행하지 않는다. 한 요청 묶음의 중복·중첩 범위는 제거하여 아직 제공하지 않은 원문만 추가한다. 전부 이미 제공한 범위라면 already_provided 상태를 명시하고 판정 또는 다른 범위 요청을 요구한다. 새 문자가 없는 요청이 2라운드 연속 이어지면 오류이며, 새 범위가 추가되면 해당 budget을 초기화한다. 실패한 출처와 잘못된 범위는 즉시 오류다. Reviewer 오류 시 초안은 검증 미통과 진단 출력으로만 보존하고 원래 실패를 유지한다. 구조 및 제한은 docs/verifier.md와 docs/TOOL_RESULT_PAGINATION.md에 따른다.

### 6.2 Model semantic review

Factual reviewer는 `evidence_verdict`, `claims[]`, `action_verdict`, `reasons`, `retry_summary`를 반환한다. 숫자 검토가 추가되면 `numeric_verdict`·`numeric_reason`도 필수다. 선택적 quality reviewer는 `alignment_verdict`, `coverage_verdict`, `coverage_reasons`, `reasons`를 반환한다. 두 호출의 결과를 합쳐 다음 축으로 다룬다.

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

각 claim에는 `evidence_refs`(source_id, location, quote)를 포함할 수 있다.
인용은 선택적 보조 정보이며, 없거나 형식이 틀려도 답변 생성을 중단하지 않는다.
없는 근거를 반대 사실이나 대체 수치로 발명하면 안 된다.
Runtime은 성공한 tool의 원래 실행 index와 user/검증된
이전 대화/current time 출처를 기준으로 source ID 및 인용문의 존재를 확인한다.
인용 위치의 의미적 정확성과 계산의 정확성까지 보장하는 검사는 아니다.
누락·잘못된 형식의 인용은 빈 목록으로 처리하고, 원문과 일치하지 않는 인용은 개별적으로
제외해 경고를 기록한다. 인용 메타데이터 때문에 reviewer를 재호출하거나 예외를 발생시키지
않으며, 원래 사실 판정과 수정 흐름을 유지한다. 유효한 인용은 correction contract와
audit에 보존한다. 상세 source ID 형식과 제한은 [`docs/verifier.md`](docs/verifier.md)를 따른다.

같은 성공 evidence 상태로 grounding 반려가 반복되면 단순 문장/수치 교체 대신 원본
재확인, 계산/검증 접근 변경, 또는 주장 축소/삭제를 지시한다. 동일 결과를 다시 호출한
것은 새 evidence로 세지 않는다. 별도 의미 분류기나 강제 검색 라운드는 추가하지 않는다.

Main runtime과 factual reviewer는 이름의 정체를 미리 단정하지 않고, 검색 결과가 가정과
충돌하면 가정을 재검토하도록 안내한다. 사용자가 요청한 근거 기반 해석/은유와 저자/제조사가
공식적으로 밝힌 의도는 구분한다. 이는 prompt 정책이며 실제 모델의 준수율은 별도 평가한다.

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

Coverage correction budget은 grounding/semantic budget과 별도이며 **최대 2회**다. 소진 후에도 coverage가 미달이면 명시적으로 실패한다. 검증 축을 끄거나 실패 후보를 통과시키지 않는다. Numeric 2회, semantic 기본 2회, evidence/action 5회 예산도 각각 적용한다. 배포 `settings.yaml`은 quality review를 끄므로 coverage 검토도 기본 실행하지 않는다.

### 6.5 Action outcome

Mutation tool invocation의 success는 그 tool contract가 성공했다는 evidence다. 더 넓은 requested end state까지 완료됐다고 주장하려면 같은 scope의 authoritative result 또는 resulting-state evidence가 필요하다.

### 6.6 Reviewer failure

Reviewer timeout, structured-output schema violation, reviewer exception은 명시적으로 log하고 semantic 내용을 문자열 fallback으로 복원하지 않는다.

Provider/structured-output 오류에는 구현된 retry budget을 적용한다. 소진 후 오류는 전파하며, 원문 재열람 계약 위반도 실패로 드러낸다. 장애를 supported 판정으로 바꾸거나 문자열 fallback으로 성공을 만들지 않는다.

---

## 7. Fatal failure와 진단 출력

Fatal failure는 `AgentRunFailure`로 원래 오류 종류·라운드·관측된 tool execution을 보존한다. Verifier 장애 중의 현재 후보 또는 마지막 거절 후보가 있으면 검증 미통과 진단으로 함께 보존할 수 있다.

Web 응답/job에는 오류와 `unverified_answer`, `verifier_feedback` 등을 표시할 수 있지만 승인된 최종 답변으로 취급하지 않는다. 별도 failure finalizer 모델 호출로 성공 답변을 만드는 경로는 현재 없다.

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

브라우저는 Bearer token과 로그인 전 실패 횟수 구분용 token을 `sessionStorage`에 저장한다. Password와 마지막 성공 `user_id`를 localStorage에 보관하는 경로는 현재 없다. 로그인 전 token은 `GET /login/session`의 JSON 응답으로 받고 `X-MAI-Preauth-Session` 헤더로 전송한다. 쿠키 방식이 아니다. 같은 세션의 인증 실패 10회는 1시간 잠금을 만들며, 실패 기록은 로그 DB에 해시로 보존한다. 상세는 [로그인 제한](docs/login-ip-lockout.md)을 따른다.

---

## 10. Persistent chat contract

Web UI persistent conversation ownership은 `db_id` 기준이다.

`CHAT_DB_PATH` 안에서 Web UI는 전용 `web_chat_messages` 테이블을 사용한다.

Startup migration은 이미 알려진 legacy persistent-chat schema만 구조적으로 이관한다. 같은 DB 안의 unrelated `chat_messages`는 임의 변형하거나 삭제하지 않는다. `web_chat_messages` 자체가 알 수 없는 schema면 startup에서 실패한다.

UI 전체 history와 model context는 별개다. Model에는 최근 `SESSION_HISTORY_MESSAGES`개만 전달할 수 있다.

Running resumable chat job은 process memory에 있다. Browser/device disconnect를 넘겨 실행될 수 있지만 server process restart를 넘겨 계산 자체가 이어지지는 않는다. 완료된 assistant message는 persistent chat에 저장된다.

---

## 11. Permanent memory graph

새 생산 경로는 `anchor`, `fact`, `concept` 노드를 만들며 다음 연결을 사용한다.

```text
user_anchor -> fact : asserted_fact (user_assertion)
fact -> concept    : mentions (fact_index)
```

사용자 원문은 별도 `evidence` 테이블에 보관한다. 현재 새 Fact에서 원문 번호로 가는 연결은 만들지 않는다. `utterance` 타입과 `spoke`·`derived_fact` 같은 옛 관계는 기존 DB/저장소 API에 남아 있지만 매 턴 새 Utterance를 생성하지 않는다.

저장 시 Sentence_Breaker가 Fact를 canonical segment로 나누며 Concept identity를 정한다. 조회 시에는 검색어를 공백으로 나눠 exact hash와 SQLite FTS5로 검색한다. 검색에서 Sentence_Breaker를 다시 호출하지 않으며 FTS 결과로 Concept identity를 합치지 않는다. 옛 vector/sqlite-vec 테이블도 자동 삭제하지 않는다.

## 12. Memory tool contract

단일 native 도구 `memory_search`가 세 모드를 제공한다.

| 입력 | 동작 |
|---|---|
| `keyword`, 선택적 양수 `limit` | 주제 검색. 최종 Fact 수에 limit 적용 |
| 선택자 없이 `limit` | 최근 목록. 필수 1~50이며 기본값 없음 |
| `node_id`만 | one-hop 확장과 현재 anchor까지 경로. `limit`·`keyword` 동시 지정 불가 |

주제 검색은 chunk별·전체 Concept seed 기본 최대 12개를 사용하고 현재 사용자 Fact를 필터링한다. 자신의 anchor 확장은 기본 5개 Fact이며 다른 사용자 anchor는 거부한다. 일반 노드 확장의 모든 이웃·경로에 동일한 소유권 필터가 있는 것은 아니다.

모델용 snapshot에는 Fact/Utterance의 내용·번호·생성 시각·출처 정보만 담고 Concept/edges는 숨긴다. 내부 그래프와 모델 출력은 다르다. 상세는 [MEMORY_V1](docs/MEMORY_V1.md)을 따른다.

## 13. Post-response memory write

승인된 답변을 얻은 뒤 같은 서버의 background 작업으로 장기기억을 기록한다. 같은 `db_id`의 다음 요청은 이전 작업을 기다린다. 종료 시 취소하며 durable queue로 재개하지 않는다.

선택된 chat model을 `think=False`, 도구 없음으로 호출한다. 입력은 최신 사용자 원문, 직전 assistant 메시지, 현재 최종 답변, 성공한 비기억조회 실행의 최초 `context_content`다. 현재 최종 답변은 문맥 전용이며, 사용자의 폭넓은 동의는 같은 주제의 직전 답변에만 적용할 수 있다.

출력은 `{"facts": [string, ...]}`다. 별도 사용자 발췌 선별·허용 출처 번호·검증된 최종 주장 목록을 전달하지 않는다. 성공한 `memory_search` 결과는 제외하며, 현재 admission 필터는 `tool_manual`·`tool_result_read`를 별도로 제외하지 않는다.

`memory_search` 성공 + extraction 성공 + 새 Fact 없음이면 원문·그래프 쓰기를 생략한다. 추출 실패는 경고로 드러내고 원문 보관을 시도한다. 원문·Fact·Concept·색인 전체를 한 트랜잭션으로 묶지는 않는다.

## 14. Owner / Trial model contract

Owner는 선택한 백엔드가 제공하는 모델 목록에서 선택할 수 있다. Ollama는 설치 모델, llama.cpp는 `/v1/models` 목록을 사용한다.

Trial은 configured `MAIN_MODEL` 하나로 고정한다. UI 제한에 의존하지 않고 server boundary에서 다른 model request를 거부한다.

Trial용 별도 model setting은 두지 않는다.

---

## 15. Filesystem / upload permissions

Owner local tools는 repository confinement를 기본 전제로 하지 않는다. OS 계정이 접근 가능한 절대경로를 정식 입력으로 사용할 수 있다.

Trial은 PC-wide read/discovery는 가능하지만 arbitrary mutation은 불가하다.

```text
Trial read
  file_list / file_search / file_read
    └─ text / PDF / DOCX / XLSX / CSV / PPTX
  code_search / code_read / code_symbols
  csv_aggregate
  image_analyze  # configured only

Trial mutation
  file_write
  └─ own upload directory boundary only

Trial unavailable
  file_delete / file_move / file_copy
  terminal_run
```

Trial upload ownership/path derivation은 `db_id` 기준이다. Resolved path boundary는 handler에서 강제한다.

---

## 16. File / image / web / market / time

`file_read`는 model-facing local read entrypoint다.

```text
plain text
PDF
DOCX
XLSX
CSV
PPTX
```

일반 텍스트는 직접 읽고, 구조화 문서는 내부 parser가 처리한 뒤 동일한 `file_read` 결과 계약으로 반환한다. CSV는 기본 UTF-8 BOM 호환 인코딩을 사용하며 필요하면 `cp949` 같은 명시적 인코딩을 전달할 수 있다.

모델에게 `file_read`와 별도의 `document_read` 사이에서 형식별 route를 고르게 하지 않는다. 문서 parser failure를 일반 텍스트 read 성공으로 위장하는 fallback도 두지 않는다.

`image_analyze`는 `VISION_MODEL`이 설정된 경우에만 registry에 등록한다.

`web_fetch`는 public HTTP(S)만 허용하고 loopback/private target 및 redirect destination을 검사한다.

`market_data`는 현재 한국 상장주식 current quote/valuation read-only source를 사용한다. Protocol drift는 failure로 드러낸다.

`current_time()`은 host OS의 local/UTC time과 timezone/offset을 제공한다. Timezone을 문자열 query heuristic으로 추정하지 않는다.

---

## 17. Tailscale Funnel

Remote public access는 `TAILSCALE_FUNNEL=true`로 구성한다.

Legacy `TAILSCALE_SERVE=true`는 retired config이며 조용히 다른 의미로 처리하지 않는다.

Funnel 시작의 `TailscaleFunnelError`는 예외 로그에 남기고 로컬 서버는 계속 실행한다. `/health`의 `tailscale_funnel`은 `false`이며 공개 연결 성공으로 처리하지 않는다.

---

## 18. Failure principle

필수 contract violation은 실패로 드러내는 것을 기본으로 한다.

금지:

- 오류 문자열을 비교해 semantic workaround 선택
- 실패를 성공처럼 포장하는 fallback
- helper가 main response contract를 우회
- 특정 주제/관계/identity를 문자열 rule로 판정

본 모델이 근거 범위에 맞는 부분 답변으로 교정하여 검증을 통과할 수는 있다. Fatal failure 뒤에 helper가 승인된 답변을 대신 생성하는 경로는 없다.

## 19. 설정의 실제 적용 범위

배포 기본값은 `settings.yaml`, 개인 계정·경로·공개 설정은 `.env`에 둔다. 기존 프로세스 환경변수 > `.env` > YAML 순으로 적용한다. YAML의 timeout은 500초, main Ollama 출력 한도는 32,768토큰, 한 번의 도구 결과 입력은 8,000자, 최근 대화는 메시지 10개, quality review는 `false`다.

`OLLAMA_THINK`, `MAI_DEBUG_MODEL_INPUT`, `TOOL_TIMEOUT_SECONDS`, `TERMINAL_TIMEOUT_SECONDS`, `MEMORY_RECALL_INCLUDE_UTTERANCES`는 현재 설정 로더가 허용하지만 실행 경로에 값을 읽는 소비자가 없다. 값을 바꾸면 동작이 바뀐다고 안내해서는 안 된다. Main adapter는 `think=True`로 구성하고 구체적 도구 timeout은 등록 함수 인자로 정한다.

Owner PC 도구는 같은 이름에 `is_remote`를 추가한다. 생략/`false`는 MAI 호스트, `true`는 해당 계정의 활성 원격 세션에서 실행한다. 세션이 없으면 오류이며 로컬로 대체하지 않는다. [원격 연결](docs/remote-powershell-connection.md)을 참고한다.
