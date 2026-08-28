# MAI MyAI sLLM — Working Contract

이 문서는 README의 소개를 반복하는 문서가 아니라, 현재 MAI runtime을 구현할 때 지켜야 하는 **구조적 계약**을 기록한다.

프로젝트 설명은 [`README.md`](README.md), memory schema와 recall 구조는 [`MEMORY_V1.md`](MEMORY_V1.md), Web UI/Tailscale 실행은 [`RUN_UI.md`](RUN_UI.md)를 기준으로 한다.

---

# 1. Production runtime

현재 production 경로는 **pure-agent C**다.

```text
Authenticated User
   ↓
AccessPrincipal
   ↓
Main LLM
   ↓ native tool selection
ToolRegistry
   ↓
Final Response
   ↓
Post-response Memory Update
```

production 경로에는 Tool Requirement Preflight와 automatic recall을 두지 않는다. 기억도 일반 native tool capability로 취급하고 모델이 필요할 때 직접 호출한다.

A 경로와 관련된 preflight/required-tool 코드가 source tree에 남아 있을 수 있지만, 그것은 현재 production request path의 계약이 아니다.

Framework는 `if text contains ...` 형태의 문자열 heuristic으로 사람, 정체성, 주제, 관계, correction 의도, route, tool 필요 여부를 판정하지 않는다.

---

# 2. Runtime 역할 분리

```text
Ollama / Model
  = inference, reasoning, native tool_calls

OllamaAdapter
  = Ollama protocol translation

AgentRuntime
  = model/tool multi-round loop + guards

ToolRegistry
  = native schema + executable binding + timeout

MemoryRuntime
  = evidence / recall / graph write lifecycle

AccessPolicy
  = auth identity / memory identity / role

FastAPI Server
  = login session / model access / upload / chat boundary
```

의미 판단은 모델이 담당하고 framework는 실행·권한·데이터 계약을 강제한다.

---

# 3. Ollama native tool contract

MAI는 Ollama의 `tools` / `tool_calls` 구조를 그대로 사용한다.

`OllamaAdapter`는 tool을 실행하지 않는다. Tool call을 자체 문자열 JSON으로 다시 만들거나 `<think>` 문자열을 파싱해 의미를 복원하지 않는다.

Native response가 schema/protocol contract를 위반하면 임의 보정하지 않고 실패시킨다.

---

# 4. Tool Registry

모든 model-visible capability는 `ToolRegistry`에 등록한다.

```text
ToolDefinition
  name
  description
  Pydantic input schema
  handler
  timeout
  category
```

현재 주요 category는 다음과 같다.

```text
memory
time
filesystem
code
document
image
web
market
terminal
```

Registry는 tool 이름의 의미를 문자열로 해석하지 않는다. Schema validation 실패, handler exception, timeout은 구조화된 실패로 반환한다.

현재 `tool_manual`은 production tool로 구현되어 있지 않으므로 capability 목록에 존재하는 것처럼 문서화하지 않는다.

---

# 5. Memory tool contract

현재 model-visible memory entry는 다음 세 가지다.

```text
memory_overview(limit)
  = 특정 lexical query 없이 현재 memory identity의 넓은 개요

memory_recall(query)
  = 특정 주제의 ConceptIndex 기반 recall

memory_search(node_id)
  = 선택한 permanent node의 정확히 one-hop 확장
```

`memory_search` helper가 임의 깊이 traversal을 숨겨 수행하지 않는다. 더 깊은 탐색이 필요하면 모델이 추가 memory call을 한다.

---

# 6. Permanent memory graph

Memory는 chat history나 vector store가 아니다.

```text
Permanent Graph
  = long-term memory body

ConceptIndex
  = graph entry point

Working Graph
  = recalled subgraph representation
```

핵심 node:

```text
User Anchor
Utterance
Fact
Concept
```

핵심 relation:

```text
user_anchor -> utterance : spoke
user_anchor -> fact      : asserted_fact
utterance   -> fact      : derived_fact
utterance   -> concept   : mentions
fact        -> concept   : mentions
```

원문 Utterance는 Fact와 분리해 보존한다. Fact는 원문을 덮어쓰지 않는다.

---

# 7. Concept identity / retrieval

Sentence_Breaker가 만든 canonical segment가 Concept identity를 결정한다.

```text
one canonical segment = one Concept Node
```

Semantic similarity나 FTS 검색 결과가 Concept identity를 합치지 않는다.

Persistent recall은 embedding/vector space를 사용하지 않는다.

```text
query
  ↓ Sentence_Breaker
segments
  ↓
exact hash lookup
  ↓ miss
SQLite FTS5 lexical retrieval
  ↓
Concept Node IDs
```

Exact mapping은 persistent SQLite와 runtime dictionary/hash를 사용한다. FTS5는 lexical candidate retrieval만 담당한다.

기존 DB에 legacy sqlite-vec table이 남아 있어도 자동 삭제하지 않는다.

---

# 8. Auth identity와 memory identity

Authentication identity와 graph memory identity는 별개의 계약이다.

```text
AccessPrincipal
  auth_user_id
  memory_user_id
  role
```

Owner mapping:

```text
OWNER_ID -> OWNER_MEMORY_ID
```

Trial mapping:

```text
trial login ID -> same trial memory ID
```

`OWNER_MEMORY_ID`는 필수다. `OWNER_ID`로 silent fallback하지 않는다. 누락 시 startup이 실패해야 한다.

Owner memory identity가 trial ID와 충돌해서 동일 User Anchor를 공유하게 되는 설정도 startup에서 거부한다.

Chat history session key는 auth identity를 사용하고 permanent graph memory access는 memory identity를 사용한다.

---

# 9. Owner / Trial model contract

Owner는 설치된 Ollama model 목록을 보고 request별 model을 선택할 수 있다.

Trial은 **현재 runtime의 configured default model (`MAIN_MODEL`) 하나로 고정**한다.

```text
GET /models
owner -> installed models
trial -> [MAIN_MODEL]
```

UI에서 selector를 숨기거나 하나만 보여주는 것만으로 권한을 구현하지 않는다. Trial client가 `/chat`에 다른 `model` 값을 직접 보내도 서버에서 HTTP 403으로 거부해야 한다.

Trial이 model을 생략하거나 `MAIN_MODEL`과 동일한 값을 보내는 것은 허용한다.

별도의 `TRIAL_MODEL`을 두지 않는다. Trial model은 `MAIN_MODEL` 설정을 따른다.

---

# 10. PC read/write access contract

Owner의 filesystem, document, image, code, terminal 도구는 repository confinement를 기본 전제로 하지 않는다.

절대경로는 정식 입력이다. 상대경로는 runtime `cwd` 기준으로 해석한다.

OS 계정이 접근할 수 없는 경로는 권한 오류로 실패해야 한다.

Trial은 PC-wide read/discovery capability를 사용할 수 있지만 arbitrary mutation은 허용하지 않는다.

```text
Trial read
  file_list / file_search / file_read
  code_search / code_read / code_symbols
  document_read
  image_analyze

Trial write
  file_write / file_create
  └─ resolved path가 mai_uploads 내부일 때만

Trial unavailable
  file_delete / file_move / file_copy
  terminal_run
```

Trial upload write restriction은 prompt가 아니라 handler의 resolved-path boundary에서 강제한다. `..`, symlink/relative traversal 등으로 resolved path가 boundary 밖이 되면 실패해야 한다.

---

# 11. File/code discovery contract

```text
file_list / file_search
  = path와 filename 탐색

file_read
  = text file read

code_search
  = source content 검색

code_read
  = line range 보존 read

code_symbols
  = parser가 지원하는 구조적 symbol 탐색
```

`code_search`의 literal/regex mode, case sensitivity, include/exclude glob, encoding, file size limit은 명시적 입력이다. Runtime이 문자열 모양을 보고 regex 여부를 자동 추측하지 않는다.

`code_symbols`는 지원 parser가 없는 언어를 regex로 class/function처럼 꾸며 반환하지 않는다. 현재 Python symbol 탐색은 AST를 사용한다.

---

# 12. Structured document contract

`document_read`는 현재 다음 형식을 지원한다.

```text
PDF
DOCX
XLSX
CSV
PPTX
```

CSV encoding 기본값은 `utf-8-sig`다. CP949 등 다른 encoding이 필요하면 명시적으로 전달한다. Encoding을 문자열 heuristic으로 추측하지 않는다.

문서 read 실패를 `file_read` fallback으로 성공처럼 위장하지 않는다.

---

# 13. Image contract

`image_analyze`는 별도로 설정된 Ollama vision model을 사용한다.

```env
VISION_MODEL=<installed vision model>
```

`VISION_MODEL`이 비어 있으면 `image_analyze` 자체를 ToolRegistry에 등록하지 않는다. Vision capability가 없는 상황에서 generic text model로 이미지 분석을 가장하지 않는다.

---

# 14. Web / market contract

`web_search`는 general/recent web discovery를 담당한다.

`web_fetch`는 이미 알고 있는 public HTTP(S) URL의 실제 본문을 읽는다. Loopback/private-network 주소를 거부하고 redirect destination도 다시 검사한다.

`market_data`는 한국 상장주식의 current quote/valuation용 structured tool이다. 현재 source는 Naver Finance의 read-only frontend endpoint이며 protocol drift는 명시적 실패로 드러낸다.

Web/search/market tool을 terminal command fallback으로 우회하지 않는다.

---

# 15. Current time contract

`current_time()`은 zero-argument native tool이다.

Host OS의 현재 local time과 UTC time, timezone/offset을 반환한다. 현재 timezone source는 OS configuration이며 문자열 query로 timezone을 추정하지 않는다.

---

# 16. Upload contract

Web UI와 API의 authenticated owner/trial 모두 파일을 업로드할 수 있다.

저장 위치:

```text
./mai_uploads/
```

Runtime startup에서 폴더를 생성하고 repository `.gitignore`는 `mai_uploads/` 전체를 제외한다.

Upload filename은 plain filename이어야 하며 path separator를 허용하지 않는다. 기존 파일은 조용히 overwrite하지 않고 HTTP 409로 실패한다.

Upload 중 실패하면 partial target을 제거하고 오류를 숨기지 않는다.

---

# 17. Tailscale Funnel contract

MAI의 remote access mode는 public Tailscale Funnel이다.

```env
TAILSCALE_FUNNEL=true
```

구성은 Tailscale CLI의 background Funnel을 사용한다.

```bash
tailscale funnel --bg --yes <MAI_PORT>
tailscale funnel status
```

`tailscale funnel status` 결과를 startup terminal에 출력해 public URL을 확인할 수 있어야 한다.

과거 `TAILSCALE_SERVE=true`는 retired configuration이다. True로 남아 있으면 tailnet-only Serve로 조용히 실행하지 않고 startup에서 실패한다.

Funnel CLI 실패, permission 문제, status 실패는 MAI startup 실패로 드러낸다.

Background Funnel은 MAI process 종료 시 자동 reset하지 않는다.

---

# 18. Agent guards

Agent loop는 최소한 다음 구조적 반복을 감시한다.

```text
maximum model rounds
identical call repetition
identical failure repetition
structural no-progress repetition
```

Guard는 tool 호출 의미를 문자열로 추측해 route를 바꾸는 장치가 아니다.

---

# 19. Failure semantics

다음은 명시적 실패다.

```text
invalid native tool schema
unknown tool
Pydantic validation failure
file not found
permission denied
trial write boundary violation
trial unauthorized model selection
invalid regex in explicit regex mode
unsupported structured parser/document
vision model unavailable
terminal non-zero return code
terminal timeout
web/network failure
private URL rejection
SQLite / FTS5 failure
Concept identity conflict
identity collision
Tailscale Funnel failure
```

문자열 비교, 임시 우회, fallback으로 contract violation을 숨기지 않는다.

---

# 20. Post-response memory write

Long-term graph mutation은 main agent/tool loop와 분리한다.

```text
raw user evidence
  ↓
agent/tool loop
  ↓
final response
  ↓
MemoryRuntime.finish_turn()
```

현재 production runtime은 raw Utterance와 Concept 연결을 보존한다. `fact_extractor=None` 상태에서는 semantic Fact extraction이 수행되지 않는다. 이를 이미 Fact memory가 완성된 것처럼 문서화하거나 fallback으로 숨기지 않는다.

Tool/search에서 얻은 world fact를 사용자 assertion으로 자동 저장하지 않는다.

---

# 21. Correction / conflict 원칙

기억은 저장됐다는 이유만으로 영구적인 단일 진실이 되지 않는다. 사용자 정정과 충돌은 원래 evidence를 삭제해 덮어쓰기보다 source/provenance를 보존하고 version/conflict relation으로 표현하는 것을 원칙으로 한다.

최소한 다음 의미를 구분한다.

```text
user model correction
content / factual correction
response style correction
```

이 원칙은 확정되어 있지만 현재 production runtime에 correction application 전체가 연결되어 있다고 가정하지 않는다.
