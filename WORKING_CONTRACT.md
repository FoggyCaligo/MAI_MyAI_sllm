# MAI MyAI sLLM — Working Contract

이 문서는 README의 소개를 반복하는 문서가 아니라, MAI runtime을 구현할 때 지켜야 하는 **구조적 계약**을 기록한다.

프로젝트의 목적과 차별점은 [`README.md`](README.md), 장기기억의 세부 schema와 recall 계약은 [`MEMORY_V1.md`](MEMORY_V1.md)를 기준으로 한다.

---

# 1. Runtime 역할 분리

```text
Ollama / Model
  = inference, reasoning, native tool_calls

OllamaAdapter
  = Ollama protocol translation

AgentRuntime
  = model/tool multi-round loop, validation, guards

MemoryRuntime
  = evidence, recall, graph write lifecycle

ToolRegistry
  = native schema + executable binding + timeout

Tool implementations
  = memory / filesystem / code / document / image / web / terminal
```

LLM이 의미 판단을 담당하고 framework는 실행 계약과 데이터 구조를 강제한다. Framework가 `if text contains ...` 형태의 문자열 heuristic으로 사람, 주제, 관계, tool 필요 여부를 판정하지 않는다.

---

# 2. Ollama native tool contract

MAI는 Ollama의 `tools` / `tool_calls` 구조를 그대로 사용한다.

```text
AgentRuntime
   ↓
OllamaAdapter.chat(messages, tools)
   ↓
Ollama
   ├─ content
   ├─ thinking
   └─ tool_calls
```

`OllamaAdapter`는 tool을 실행하지 않는다. `tool_calls`를 자체 문자열 JSON으로 다시 만들거나 `<think>` 문자열을 파싱해 의미를 복원하지 않는다.

Native response가 contract를 위반하면 임의 보정하지 않고 protocol error로 실패시킨다.

---

# 3. Tool Registry

모든 실행 capability는 `ToolRegistry`에 등록한다.

```text
ToolDefinition
  name
  description
  Pydantic input schema
  handler
  timeout
  category
```

Registry는 tool 이름의 의미를 해석하지 않는다. Schema validation 실패, handler exception, timeout은 구조화된 실패로 반환한다.

도구 범주는 다음과 같이 유지한다.

```text
memory
filesystem
code
document
image
web
terminal
tool manual
```

Filesystem / code / terminal은 현재 repository에 구현되어 있다. Document / image / web / tool manual은 기존 runtime에서 사용한 계약을 동일한 native Tool Registry 구조에 맞춰 유지한다.

---

# 4. PC 접근 계약

Filesystem, document, image, code, terminal 도구는 repository confinement를 기본 전제로 하지 않는다.

```text
MAI Process
  ├─ C:\Users\...
  ├─ 다른 drive
  ├─ 다른 Git repository
  ├─ Desktop / Documents / Downloads
  └─ PATH에서 실행 가능한 command
```

절대경로는 정식 입력이다. 상대경로는 runtime `cwd` 기준으로 해석한다.

OS 사용자가 접근할 수 없는 경로는 권한 오류로 실패해야 한다. 관리자 권한이 필요한 작업을 우회하거나 성공 문자열로 위장하지 않는다.

Terminal은 최소한 다음 실행 결과를 보존한다.

```text
stdout
stderr
returncode
timed_out
```

Timeout 시 child process가 남지 않도록 Windows에서는 process tree, POSIX에서는 process group 종료를 사용한다.

---

# 5. Code / file discovery 계약

```text
file_list / file_search
  = path와 file name 탐색

code_search
  = file content 검색

code_read
  = line range 보존 read

code_symbols
  = parser가 지원하는 구조적 symbol 탐색
```

`code_search`의 literal/regex mode, case sensitivity, include/exclude glob, encoding, file size limit은 명시적 입력이다. Runtime이 문자열 모양을 보고 regex인지 자동 추측하지 않는다.

`code_symbols`는 지원 parser가 없는 언어를 regex로 class/function처럼 꾸며 반환하지 않는다. 현재 구현은 Python AST를 사용한다.

---

# 6. Memory의 역할

Memory는 단순 chat history나 vector store가 아니다.

```text
Permanent Graph
  = long-term memory body

ConceptIndex
  = permanent graph entry point

Working Graph
  = current turn-local recalled subgraph
```

Permanent Graph는 다음 핵심 node를 사용한다.

```text
User Anchor
Utterance
Fact
Concept
```

핵심 relation은 다음과 같다.

```text
user_anchor -> utterance : spoke
user_anchor -> fact      : asserted_fact
utterance   -> fact      : derived_fact
utterance   -> concept   : mentions
fact        -> concept   : mentions
```

원문 Utterance는 Fact와 분리해 보존한다. Fact는 원문을 대체하지 않는다.

---

# 7. Concept identity와 Sentence_Breaker

Sentence_Breaker가 만든 canonical segment가 Concept identity를 결정한다.

```text
one canonical segment = one Concept Node
```

Semantic similarity나 lexical search 결과가 Concept identity를 합치지 않는다.

동일 Concept이 여러 발화와 Fact에서 반복되면 하나의 Concept Node를 공유한다.

---

# 8. ConceptIndex: Exact + SQLite FTS5

Persistent memory recall은 embedding model이나 vector space에 의존하지 않는다.

```text
query
  ↓ Sentence_Breaker
segments
  ↓
exact hash lookup
  ↓ miss
SQLite FTS5 lexical search
  ↓
Concept Node IDs
```

Exact mapping은 SQLite에 저장되고 runtime에서 dictionary로 읽어 hash lookup을 수행한다. FTS5는 lexical candidate retrieval만 담당한다.

Index는 graph identity를 소유하지 않는다. Graph가 Concept Node identity를 소유한다.

기존 graph DB를 열 때 ConceptIndex에 없는 Concept Node는 non-destructive하게 동기화한다. 이전 개발 DB에 sqlite-vec table이 남아 있어도 자동 삭제하지 않는다.

---

# 9. User Anchor와 provenance

모든 사용자 memory recall은 현재 `user_id`의 User Anchor로 다시 연결될 수 있어야 한다.

이 계약은 `나`, `내`, 사용자 프로젝트·선호 같은 1인칭 기억이 agent 자신의 속성으로 뒤집히는 문제를 막기 위한 구조적 기준이다.

Graph path 탐색은 topology를 찾을 때 edge direction을 무시할 수 있지만, Working Graph에 반환되는 edge는 실제 저장 방향, relation, provenance를 그대로 유지한다.

---

# 10. Automatic recall

A 경로의 기본 recall은 다음 구조를 사용한다.

```text
User Input
  ↓ Sentence_Breaker
ConceptIndex
  ↓
Concept seed
  ├─ one-hop neighborhood
  └─ shortest path to current User Anchor
  ↓
Initial Working Graph
```

전체 graph를 매 턴 모델에게 주입하지 않는다. 현재 query와 연결된 작은 subgraph만 Working Graph에 올린다.

---

# 11. Deliberate memory traversal

`memory_search(node_id)`는 선택한 permanent node의 **정확히 one-hop**을 Working Graph에 merge한다.

새로 보이는 node에도 가능한 User Anchor path를 붙인다.

임의 깊이 traversal을 helper 내부에서 숨기지 않는다. 더 깊이 탐색하려면 model이 추가 memory call을 해야 한다.

C pure-agent 실험에서는 `memory_recall(query)`를 최초 memory entry tool로 추가 노출한다. A 경로의 기본 memory tool registration에는 자동으로 추가하지 않는다.

---

# 12. Tool Requirement Preflight

A 경로에서는 main agent의 첫 model call 전에 Tool Requirement Preflight가 현재 요청의 필수 capability를 판정한다.

입력 범위는 다음으로 제한한다.

```text
current user request
minimum recent dialogue
available capability catalog
minimum runtime facts
```

Preflight에는 automatic recall result, Working Graph, search result, tool result를 넣지 않는다.

출력은 tool별 boolean requirement이고 frozen obligation set으로 바꾼다.

```text
required=true
  = final answer 전에 해당 exact tool capability가 성공해야 함

required=false
  = mandatory obligation이 아님
  = tool 사용 금지가 아님
```

Tool requirement 의미 판단을 문자열 패턴으로 구현하지 않는다. Model-backed planner가 capability contract를 보고 판정한다.

---

# 13. 한 turn의 A 경로

```text
User Input
   ↓
Tool Requirement Preflight
   ↓ freeze
Raw User Evidence + User Anchor
   ↓
Automatic Recall
   ↓
Working Graph
   ↓
Agent Runtime
   ↓
Native Tool Calls
   ↓
Required Tool Success Check
   ↓
Final Response
   ↓
Post-response Memory Update
```

이 순서는 전체 프로젝트의 핵심 아이디어가 아니라 **A 실행 경로의 일관성을 위한 runtime contract**다.

---

# 14. Agent guards

Agent loop는 최소한 다음 반복 실패를 막는다.

```text
maximum model rounds
identical call repetition
identical failure repetition
structural no-progress repetition
```

Guard는 tool 호출 의미를 문자열로 추측해서 route를 바꾸는 장치가 아니다. 반복된 구조적 상태를 감시한다.

---

# 15. Failure semantics

실패를 성공처럼 보이게 만들지 않는다.

다음 상황은 명시적 실패다.

```text
invalid native tool schema
unknown tool
Pydantic validation failure
file not found
permission denied
invalid regex in explicit regex mode
unsupported structured parser
terminal non-zero return code
terminal timeout
SQLite / FTS5 failure
Concept index identity conflict
unsatisfied required tool obligation
```

문자열 비교나 fallback으로 contract 위반을 숨기지 않는다.

---

# 16. Post-response memory write

해석된 long-term graph mutation은 agent/tool loop와 분리한다.

```text
raw evidence
  ↓
agent/tool loop
  ↓
final response accepted
  ↓
MemoryRuntime.finish_turn()
```

`finish_turn()`은 다음을 담당한다.

```text
Utterance Node 생성
User Anchor -> Utterance
Utterance -> Concepts
user-grounded Fact extraction
User Anchor -> Fact
Utterance -> Fact provenance
Fact -> Concepts
new Concept -> ConceptIndex
```

사용자 Fact로 저장되는 내용은 사용자 발화에 직접 근거해야 한다. Tool/search 결과의 world fact를 사용자 assertion으로 조용히 바꾸지 않는다.

---

# 17. Correction / conflict 원칙

기억은 한 번 저장됐다는 이유만으로 절대 진실이 되지 않는다. 사용자 정정과 충돌은 기존 source를 삭제해 덮어쓰는 방식보다 원래 evidence를 보존하면서 새 provenance와 version relation으로 표현하는 것을 원칙으로 한다.

정정 대상은 최소한 다음 의미를 구분한다.

```text
user model correction
content / factual correction
response style correction
```

서로 다른 종류의 correction을 모두 사용자 profile mutation으로 처리하지 않는다.

---

# 18. 구현 상태를 읽는 방법

이 프로젝트 문서에는 두 종류의 계약이 함께 들어 있다.

1. **현재 repository에 코드가 존재하는 계약**
2. **기존 MAI runtime에서 이미 동작 방식이 정해졌고 이 repository 구조로 그대로 이식하는 계약**

현재 repository에 직접 들어와 있는 주요 구현은 다음과 같다.

```text
Ollama native adapter
Tool Registry
multi-round Agent Runtime
Agent guards
FrozenToolRequirements enforcement
PC-wide Filesystem
Code discovery
Terminal
Graph memory repository
Sentence_Breaker Concept identity
Exact + SQLite FTS5 ConceptIndex
Working Graph recall
memory_search one-hop expansion
post-response memory lifecycle boundary
```

다음 항목은 동작 방식과 계약이 정해진 구성요소로 같은 runtime 구조에서 관리한다.

```text
model-backed Tool Requirement Planner
document_read
image_analyze
web search / research
tool_manual
model-backed FactExtractor
correction / conflict application
```

이 구분은 프로젝트의 기능 범위를 둘로 나누기 위한 것이 아니라, source tree에서 현재 어느 코드가 이식 완료됐는지를 확인하기 위한 개발 상태 표시다.
