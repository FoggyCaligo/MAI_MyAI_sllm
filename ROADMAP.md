# Mai Runtime Roadmap

Mai는 작은 로컬 모델이 실제 사용 중에도 파일/웹/장기기억을 안정적으로 다룰 수 있는 단일 Agent runtime을 목표로 한다.

핵심 원칙은 다음과 같다.

> 모델이 의미를 결정하고, Framework는 구조를 강제한다.

문자열 휴리스틱, hidden retry, fallback synthesis로 구조적 실패를 숨기지 않는다.

## Memory/source model

현재 target architecture는 세 저장소를 따로 두지 않는다.

```text
recent raw conversation = 짧은 대화 continuity
persistent semantic graph = 장기기억 + Agent가 직접 수정하는 working-memory substrate
turn ViewedGraph = 이번 turn에서 실제로 펼쳐 본 persistent graph의 누적 view
```

Request-scoped scratchpad는 사용하지 않는다.

### Mandatory vector recall

매 user turn의 첫 Agent reasoning은 최소 한 번의 `memory/recall(query=...)`를 거친다.

- query는 Agent가 작성한다.
- `.env`의 embedding model이 vector similarity candidate를 만든다.
- candidate 검색만으로 one-hop 전체를 자동 주입하지 않는다.
- 모델이 `memory/recall(node_id=...)`로 선택한 node만 active one-hop과 함께 ViewedGraph에 열린다.
- 다른 node를 추가로 recall하면 기존 ViewedGraph에 누적된다.
- embedding 실패를 lexical lookup으로 자동 fallback하지 않는다.

Reference configuration:

```env
MAI_OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```

### Live graph mutation

Graph mutation은 답변 뒤 별도 Memory loop에서 하지 않는다.
같은 Agent loop가 필요할 때 직접 실행한다.

```text
memory/recall
memory/generate/node
memory/generate/edge
memory/fix/node
memory/fix/edge
```

Accepted mutation은 매 round 실제 `graph.sqlite3`에 즉시 commit한다.
다음 Agent round와 이후 user turn에서 바로 recall할 수 있다.

### Reuse/fix first

- 새 node 생성 전에 vector query recall이 선행되어야 한다.
- 새 node 하나를 생성한 뒤 다음 새 node를 생성하려면 fresh query recall을 다시 해야 한다.
- semantic duplicate 여부는 모델이 판단한다.
- 동일 의미 node라고 판단하면 기존 node를 재사용한다.
- 나중에 duplicate가 발견되면 `memory/fix/node` merge로 정리한다.
- Framework가 alias dictionary나 문자열 포함 규칙으로 의미 동일성을 판단하지 않는다.

### Current edge model

Edge는 과거 버전 stack이 아니라 현재 directed relationship state 하나를 나타낸다.

```text
(start_node_id, end_node_id)
```

ordered pair당 current edge는 최대 하나다.
A→B와 B→A는 서로 다른 edge다.

Edge는 다음 상태를 가진다.

```text
relation
weight
personal_relevance
source_ids[]
```

`memory/fix/edge`는 기존 edge를 갱신한다.
Weight는 기존 값에 `weight_delta`를 적용한다.
Disconnect는 row 삭제가 아니라 weight를 0으로 만들어 normal active recall에서 제외한다.

Node lifetime degree에는 제한을 두지 않는다.
Turn execution budget만 둔다.

- 신규 node 최대 10 / turn
- node당 semantic edge mutation 최대 10 / turn

### Composite concept

여러 node가 함께 하나의 개념을 이룰 때 `kind=composite` node를 만들 수 있다.
Composite membership은 일반 relation 문자열이 아니라 Framework-owned structural data다.
Self-membership과 membership cycle은 실패한다.

### Personal relevance

`weight`와 `personal_relevance`는 별도 축이다.

```text
user_centered      -> 1.0
general_knowledge  -> 0.5
```

분류는 Agent가 한다.
Framework는 문장 키워드로 분류하지 않는다.

### Source provenance

Node와 edge는 각각 독립적으로 source ID를 가진다.

Source kinds:

- `user_message`
- `assistant_message`
- `web_evidence`
- `file_evidence`
- `tool_operation`

Graph state 자체와 source 원문은 분리한다.
Model-facing representation에서는 `source_ids: [...]`로 보이고, SQLite에서는 relational source/link table을 사용한다.

## Phase 1 — Model context

유지:

- 최근 대화 context
- tool result compaction
- recent tool-operation context
- current date system injection

최근 raw chat 전체를 graph로 자동 복제하지 않는다.
장기 정보는 Agent가 memory tools를 통해 graph에 반영한다.

## Phase 2 — Agent loop

현재 방향:

- explicit Agent round 1회 = structured LLM request 1회
- hidden retry/review model call 없음
- framework tool result는 native `role=tool` 대신 framework-authored user message로 반환
- memory/file/web tool과 answer가 같은 Agent loop의 first-class action
- answer 전 `graph_synced: true` 종료 gate

Final graph sync는 별도 reasoning model을 호출하지 않는다.
같은 Agent가 answer action에서 현재 graph가 최신 durable understanding과 일치한다고 명시적으로 확인한다.
일치하지 않으면 다음 normal Agent round에서 generate/fix를 수행한다.

## Phase 3 — External tool hierarchy

처음부터 모든 tool schema를 노출하지 않는다.

```text
/file
/web
```

File 예시:

```text
/file/tree
/file/search
/file/read
/file/create
/file/update
/file/delete
/file/document
/file/image
/file/code/index
/file/code/search
/file/terminal
```

Web:

```text
/web/search
/web/market
/web/current
```

Leaf는 `/manual`, `/use`를 제공한다.
같은 Agent loop에서 사용법을 이미 아는 경우 정확한 `/.../use` route로 바로 활성화할 수 있다.
틀린 route는 structured error로 반환하고 비슷한 이름을 추측하지 않는다.

## Phase 4 — Session / authorization / working context

유지:

- owner/trial별 tool 제한
- persistent authenticated session
- request-detached chat execution
- session별 file/code working root
- trial user ID당 active session 1개
- attachment automatic read/analyze

Trial은 host filesystem/terminal/code tool을 받지 않는다.
자기 account upload directory의 첨부만 사용할 수 있다.

## Phase 5 — Fresh graph database

이번 memory redesign은 기존 graph DB migration을 목표로 하지 않는다.
기존 `data/graph.sqlite3`은 삭제하고 새 schema로 재생성한다.

새 graph schema는 처음부터 다음을 구조적으로 강제한다.

- node `kind = concept | composite`
- active/inactive node state
- node embedding storage
- directed edge `start_node_id / end_node_id`
- `UNIQUE(user_id, start_node_id, end_node_id)`
- edge weight 0.0~1.0
- personal relevance 0.5 또는 1.0
- composite membership table
- source/link tables

`chat.sqlite3`은 graph redesign과 별개이며 초기화 대상이 아니다.

## Phase 6 — Validation

새 memory subsystem 완료 전 반드시 검증할 항목:

- 첫 query recall 전 answer 불가
- vector candidate retrieval
- candidate 검색만으로 ViewedGraph 자동 확장 금지
- node-id recall의 one-hop 누적
- same-turn ViewedGraph 유지 / turn 종료 후 폐기
- graph mutation 즉시 DB commit
- fresh recall 없는 연속 new-node 생성 거절
- directed pair duplicate edge 생성 거절
- reverse edge 허용
- weight delta / disconnect
- personal relevance 승격
- composite cycle/self-membership 거절
- node merge edge collision 시 의미 선택 없이 실패
- source ownership/scope
- 정확한 external tool route와 invalid-path failure
- answer의 same-Agent graph-sync gate

MK4의 global round cap, hidden fallback synthesis, parse-success fallback처럼 현재 Mai의 fail-visible contract와 충돌하는 기능은 parity 대상으로 간주하지 않는다.
