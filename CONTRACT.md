# MAI sLLM Runtime Contract

이 문서는 `MAI_MyAI_sllm`의 최상위 제품/실행 계약이다.
Memory architecture의 상세 canonical contract는 [`docs/contracts/AGENT_GRAPH_MEMORY_CONTRACT.md`](docs/contracts/AGENT_GRAPH_MEMORY_CONTRACT.md)다.

현재 브랜치에서는 **문서를 먼저 확정한 뒤, 기존 memory 영역을 통째로 제거하고 memory 외 runtime 위에 새 구조를 다시 얹는 방식**으로 구현한다.

---

## 1. 핵심 원칙

> 모델이 의미를 결정하고, Framework는 구조를 강제한다.

Framework는 다음을 문자열 휴리스틱으로 판단하지 않는다.

- tool route
- correction intent
- 사람/AI/정체성
- node 의미 동일성
- relation 의미
- memory 생성/수정 필요 여부

`if text contains ...` 식 의미 routing은 금지한다.
Schema/DB/tool/OS 계약 위반은 fallback으로 숨기지 않고 실패로 드러낸다.

---

## 2. Agent 구조

Target runtime은 **하나의 Agent loop**만 사용한다.

```text
User
 ↓
Agent round 1
 └─ mandatory memory/recall(query)
      ↓
      vector-similar node candidates
 ↓
Agent round 2+
 ├─ memory/recall(node_id)
 │    └─ selected node + active one-hop → ViewedGraph에 누적
 ├─ memory/generate/*
 ├─ memory/fix/*
 ├─ file/web/terminal/etc
 └─ answer
```

각 explicit Agent round는 LLM structured request 1회다.
Model adapter 내부에서 hidden retry/review/reconsideration request를 만들지 않는다.

별도 post-answer Memory loop는 없다.
Dedicated Qwen memory model도 필수 구성요소가 아니다.

---

## 3. Mandatory memory recall

매 user turn의 첫 Agent round에서는 `answer`를 허용하지 않고 최소 1회 `memory/recall(query=...)`를 수행해야 한다.

목적은 최근 raw chat 범위 밖의 장기기억이 있는데도 작은 sLLM이 바로 “기억이 없다”고 답하는 문제를 구조적으로 막는 것이다.

첫 query는 모델이 작성한다.
Framework가 user text의 의미를 해석해서 query를 생성하지 않는다.

Query recall은 `.env`에서 지정한 embedding model로 vector similarity 후보를 반환한다.
Lexical fallback은 사용하지 않는다.

Reference configuration:

```env
MAI_OLLAMA_MODEL=gemma4:e4b
MAI_OLLAMA_EMBEDDING_MODEL=nomic-embed-text
MAI_OLLAMA_IMAGE_MODEL=gemma4:12b
```

Embedding model은 reasoning model이 아니라 vector 생성용이다.

---

## 4. Turn ViewedGraph

한 turn에는 `ViewedGraph`가 하나 존재한다.

- turn 시작 시 empty
- `memory/recall(query)` 후보 검색만으로는 one-hop을 자동 주입하지 않음
- 모델이 `memory/recall(node_id)`로 node를 선택하면 해당 node + active one-hop을 ViewedGraph에 추가
- 이후 다른 node를 recall하면 기존 ViewedGraph에 합쳐짐
- generate/fix 후 영향받은 부분은 committed graph 상태로 갱신
- turn 종료/실패 시 ViewedGraph 자체는 폐기
- persistent graph mutation은 그대로 DB에 남음

ViewedGraph는 별도 DB가 아니라, **현재 turn에서 모델이 실제로 펼쳐 본 persistent graph의 누적 view**다.

---

## 5. Persistent graph memory

Graph는 long-term memory이자 Agent가 작업 중 직접 수정하는 memory다.

Mutation은 매 round 즉시 SQLite에 commit한다.

```text
round N: memory/fix
→ DB commit
→ round N+1 recall에서 즉시 확인 가능
→ 미래 turn에서도 recall 가능
```

잘못된 중간 판단도 commit될 수 있다. 이 구조에서는 transaction rollback으로 숨기지 않고, Agent가 이후 `memory/fix/*`로 현재 graph를 고치는 것을 기본으로 한다.

---

## 6. Memory namespace

```text
memory/
├─ recall
├─ generate/
│  ├─ node
│  └─ edge
└─ fix/
   ├─ node
   └─ edge
```

### `memory/recall`

- `query`: vector-similar candidate nodes
- `node_id`: 해당 node + active one-hop

### `memory/generate/node`

- concept/composite node 생성
- relevant query recall 선행 필수
- semantic duplicate 후보가 있다면 모델은 기존 node 재사용
- turn당 신규 node 최대 10개
- composite도 동일 budget 소비

### `memory/generate/edge`

- directed edge 생성
- `(start_node_id, end_node_id)` ordered pair당 current semantic edge 최대 1개
- A→B와 B→A는 별개
- 동일 directed pair가 이미 있으면 generate 실패 후 `fix/edge` 사용

### `memory/fix/node`

- rename/update
- composite membership 수정
- duplicate node merge

### `memory/fix/edge`

- relation 갱신
- `weight_delta` 적용
- personal relevance 갱신/승격
- source 추가
- disconnect는 weight를 0으로 만듦

Zero-weight edge는 provenance/debug에는 남지만 normal active recall에서는 제외한다.

---

## 7. Graph model

### Node

```text
node_id
name
kind: concept | composite
source_ids[]
```

Composite는 여러 기존 node를 하나의 상위 개념으로 지칭한다.
Membership은 일반 relation 문자열이 아니라 framework-owned structural relation이다.
Self-membership과 membership cycle은 금지한다.

### Edge

```text
edge_id
start_node_id
end_node_id
relation
weight
personal_relevance
source_ids[]
```

Direction은 start/end 자체로 표현한다.

Node의 lifetime edge 총량에는 제한을 두지 않는다.
Turn execution budget만 둔다:

- 신규 node ≤ 10 / turn
- node당 semantic edge mutation ≤ 10 / turn

한 edge mutation은 start/end 양쪽 node budget을 소비한다.

---

## 8. Weight / personal relevance

`weight`와 `personal_relevance`는 별개다.

`weight`:
- 현재 directed relationship의 강도
- `fix/edge`에서 delta로 조절
- 0.0~1.0 구조 범위
- 0은 disconnected

`personal_relevance`:

```text
user_centered      → 1.0
general_knowledge  → 0.5
```

분류는 Agent가 선택한다.
Framework는 문장 키워드로 분류하지 않는다.
0.5→1.0 승격은 가능하고, 낮은 relevance source가 추가됐다는 이유만으로 1.0을 자동 강등하지 않는다.

---

## 9. Provenance

Node와 edge는 각각 독립적인 source 관계를 가진다.

모델-facing representation:

```text
source_ids: [12, 18, 44]
```

DB는 opaque JSON list 대신 relational source/link table을 사용한다.

과거 semantic edge 버전을 3겹으로 쌓는 설계는 사용하지 않는다.
Current graph는 현재 이해를 나타내고, provenance는 그 상태의 근거를 추적한다.

---

## 10. Final graph-sync gate

별도 post-answer memory review model은 만들지 않는다.

대신 같은 Agent loop의 `answer` 종료 조건에 다음 계약을 둔다.

> Agent는 현재 persistent graph / ViewedGraph가 이번 turn에서 얻은 최신 durable understanding과 일치한다고 판단한 뒤에만 최종 answer를 반환한다.

일치하지 않으면 같은 Agent loop에서 `memory/generate/*` 또는 `memory/fix/*`를 수행한 다음 다시 진행한다.

Framework는 answer 문자열과 graph를 의미적으로 비교하지 않는다.

---

## 11. External tool discovery

처음부터 모든 tool schema를 주지 않는다.

최상위 external namespace는 작게 유지한다.

```text
/file
/web
```

File은 tree/search/read/CRUD/document/image/code/terminal 등 등록된 하위 route를 lazy discovery한다.
Web은 최소 다음 category를 가진다.

```text
/web/search
/web/market
/web/current
```

Leaf는:

```text
/.../manual
/.../use
```

를 제공한다.

모델이 같은 Agent loop에서 사용법을 이미 알고 있으면 정확한 `/.../use` route를 바로 요청할 수 있다.
잘못된 route는 structured error + valid children을 반환하고, Framework가 비슷한 문자열을 추측해 실행하지 않는다.

---

## 12. Owner / Trial / PC tools

Owner는 기존의 filesystem/document/image/code/terminal 기능을 유지한다.
실제 OS/filesystem 권한이 최종 실행 경계다.

Trial은 user별 graph memory와 허용된 web/attachment 기능만 가진다.
다른 user의 graph/source/session/path를 볼 수 없다.

File action은 current-turn path provenance 등 기존 structural safety contract를 유지한다.

---

## 13. 제거 대상 memory 구조

새 구현에서 memory 영역을 다시 구축할 때 다음 runtime 구조는 제거한다.

- dedicated `MAI_OLLAMA_MEMORY_MODEL`
- post-answer `GraphCommitPhase`
- `continue_memory`
- final answer 뒤 memory mutation loop
- `ScratchpadRegistry`
- `scratchpad_put`
- `scratchpad_update`
- scratchpad → durable memory promotion
- old `node_lookup` + `recall_memory` 분리 API

Memory 외 runtime은 가능한 한 유지하고, 새 memory subsystem만 그 위에 다시 얹는다.

---

## 14. 실패 처리

다음은 성공으로 숨기지 않는다.

- embedding failure
- model/schema failure
- invalid graph ownership/scope
- invalid source
- duplicate directed edge generation
- composite cycle/self-membership
- node/edge turn budget exhaustion
- invalid tool route
- file/OS/tool failure

Semantic fallback, guessed route, malformed action 자동 교정은 사용하지 않는다.
