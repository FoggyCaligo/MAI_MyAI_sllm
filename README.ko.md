# Mai

[English](README.md) | **한국어**

> README 유지보수 원칙: `README.ko.md`를 내용 원본으로 관리하고 영어 README를 같은 구조로 동기화한다.

Mai는 **로컬 sLLM에 장기 graph memory와 실제 PC/web 도구를 붙여 개인용 semi-GPT를 만드는 프로젝트**다.

핵심 철학은 다음과 같다.

> **모델이 의미를 결정하고, Framework는 구조를 강제한다.**

현재 브랜치는 memory architecture를 **Actual Graph + turn-local Working Graph + periodic graph checkpoint** 구조로 재구축한다.

Memory 상세 기준 문서:

- [`docs/contracts/AGENT_GRAPH_MEMORY_CONTRACT.md`](docs/contracts/AGENT_GRAPH_MEMORY_CONTRACT.md)

---

# 1. 목표 구조

```text
사용자
 ↓
최근 대화 context
 ↓
Mai Agent lifecycle
 ├─ mandatory vector memory recall
 ├─ turn-local Working Graph
 ├─ memory recall / generate / fix
 ├─ file / document / image / code / terminal
 ├─ web / market / current info
 ├─ periodic graph checkpoint
 └─ final graph checkpoint → answer
```

별도 post-answer Memory LLM과 Scratchpad는 사용하지 않는다.
같은 main model이 일반 Agent round와 graph-only checkpoint에 사용된다.

Graph는 두 층으로 나뉜다.

1. **Actual Graph**: 이전에 성공적으로 완료된 turn에서 commit된 durable long-term memory
2. **Working Graph**: 현재 turn에서 실제로 recall해서 연 Actual Graph 영역 + 아직 commit되지 않은 현재 turn 변경사항

Working Graph는 이번 turn의 현재 인지/작업 공간이며 과거 기억의 증거로 취급하지 않는다.

---

# 2. 한 turn의 memory 흐름

첫 Main round에서는 바로 답변할 수 없다.
최소 한 번 `memory/recall(query)`를 사용해야 한다.

```text
User message
 ↓
Main round 1
 ↓
memory/recall(query)
 ↓
Embedding vector similarity search
 ↓
관련 node 후보 여러 개
```

Query recall은 candidate만 반환하며 Working Graph를 자동으로 열지 않는다.
모델이 필요하다고 판단한 candidate를 `memory/recall(node_id)`로 열면 그 node와 active one-hop이 Working Graph에 누적된다.

```text
recall A
→ Working = A + A active one-hop

recall B
→ Working = previous Working + B + B active one-hop
```

Main round 중 `memory/generate/*`, `memory/fix/*`를 사용하면 변경은 Actual Graph가 아니라 Working Graph에만 staging된다.
새 Working node는 framework가 발급한 음수 temporary ID를 사용하며 같은 turn의 이후 round에서 참조할 수 있다.

Main LLM 호출이 설정된 간격만큼 누적되면 framework가 graph-only checkpoint를 강제한다.
현재 기본 간격은 **Main LLM 3회**다.

```text
Main #1
Main #2
Main #3
 ↓
Graph Checkpoint
 ↓
Main #4 ...
```

Answer candidate가 나오면 호출 수와 관계없이 Final Graph Checkpoint를 반드시 거친다.
Periodic checkpoint 경계와 final checkpoint가 겹치면 한 번만 실행한다.

Final checkpoint 완료 후 Working Graph mutation set을 **LLM 호출 없이 하나의 atomic transaction**으로 Actual Graph에 commit한 뒤 answer를 반환한다.

---

# 3. Vector recall

`memory/recall(query)`는 lexical substring 검색이 아니라 embedding vector similarity로 candidate node를 찾는다.

Embedding model은 `.env`에서 별도로 지정한다.

Reference configuration:

```env
MAI_OLLAMA_MODEL=gemma4:e4b
MAI_OLLAMA_EMBEDDING_MODEL=nomic-embed-text
MAI_OLLAMA_IMAGE_MODEL=gemma4:12b
```

Embedding model은 별도 reasoning/memory LLM이 아니다.
Node/query vector를 만드는 역할만 한다.

Embedding 실패를 lexical search로 조용히 fallback하지 않는다.

---

# 4. Memory tool API

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

## `memory/recall`

- `query`: vector-similar node 후보 조회
- `node_id`: 특정 Actual node와 active one-hop을 Working Graph에 열기
- `edge_id`: edge의 Actual/Working/history 상태 조회

## `memory/generate/node`

새 semantic node 또는 composite node를 Working Graph에 만든다.
새 node 생성 전에는 fresh relevant query recall이 선행되어야 한다.
모델이 후보 중 동일 의미 node가 있다고 판단하면 새로 만들지 않고 기존 node를 재사용해야 한다.

한 turn 신규 node 상한은 **10개**다.
Composite도 동일 budget을 사용한다.

## `memory/generate/edge`

Edge 방향은 start/end로 표현한다.

```text
A -> B
B -> A
```

는 서로 다른 logical edge다.
동일 `(start_node_id, end_node_id)`에는 logical edge 하나만 존재한다.
Relation wording이 달라졌다는 이유로 parallel edge를 추가하지 않는다.

## `memory/fix/node`

- node 상태 수정
- composite membership 수정
- duplicate node merge

## `memory/fix/edge`

- 현재 relation 수정
- `weight_delta` 반영
- personal relevance 갱신
- source 추가
- disconnect

Disconnect는 edge를 지우는 대신 weight를 `0`으로 만든다.
Zero-weight edge는 provenance/debug/history에는 남지만 normal active recall에서는 제외한다.

---

# 5. Node / Edge 구조

## Node

```text
node_id
name
kind: concept | composite
source_ids[]
member_node_ids[]
pending
graph_created_at
graph_updated_at
```

Composite membership은 일반 relation 문자열이 아니라 Framework-owned structural data다.
Self-membership과 membership cycle은 허용하지 않는다.

## Edge

```text
edge_id
start_node_id
end_node_id
relation
weight
personal_relevance
current_version_id
```

Node가 lifetime 동안 가질 수 있는 edge 총량에는 제한이 없다.
Turn execution budget만 둔다.

- 신규 node: 최대 10개 / turn
- semantic edge mutation: 참여 node당 최대 10회 / turn

Edge mutation 하나는 start/end 양쪽 node budget을 소비한다.

---

# 6. Weight와 personal relevance

둘은 서로 다른 개념이다.

## Weight

현재 directed relationship 자체의 강도다.

- 범위: `0.0 ~ 1.0`
- update: 기존 값에 `+/- delta`
- `0.0`: disconnected

## Personal relevance

이 기억이 사용자에게 얼마나 직접적인지를 나타낸다.

```text
user_centered      = 1.0
general_knowledge  = 0.5
```

분류는 Agent가 판단한다.
Framework가 keyword로 분류하지 않는다.

Source reliability/confidence와 personal relevance는 별도 축이다.

---

# 7. Source / provenance / history

Node와 edge는 source evidence를 relational link로 가진다.
Edge source는 특정 committed edge version에 연결된다.

현재 turn의 Working edge와 이전 Actual history는 구분된다.

```text
actual_current
working_current
past_versions
working_state_is_past_evidence: false
```

같은 turn에서 edge를 여러 번 수정해도 최종 commit 시에는 그 turn의 최종 상태 하나만 새 committed version이 된다.
각 logical edge는 최근 **3개의 committed turn state**를 보존한다.

이 구조는 현재 turn에서 방금 만든 Working 상태를 과거 기억의 근거처럼 재사용하는 것을 막는다.

---

# 8. Working Graph mutation과 최종 commit

Memory semantic mutation은 Main/checkpoint 도중 **Working Graph에 즉시 반영**되지만 Actual Graph에는 즉시 commit하지 않는다.

```text
Main / Checkpoint
→ memory/generate 또는 memory/fix
→ Working Graph 변경
→ 다음 round에서 변경된 Working Graph를 바로 사용
```

따라서 같은 turn의 다음 Main round는 새 node/edge와 수정된 상태를 즉시 볼 수 있다.
하지만 durability boundary는 Final Graph Checkpoint 뒤의 atomic commit이다.

```text
Final Graph Checkpoint 완료
 ↓
Working Graph mutation set
 ↓
SQLite atomic transaction
 ↓
Actual Graph
 ↓
frozen answer 반환
```

Agent/checkpoint 실패 또는 commit 실패 시 Working semantic change는 Actual Graph에 승격되지 않는다.
Commit 실패는 숨기지 않으며 frozen answer도 반환하지 않는다.

---

# 9. Periodic / Final Graph Checkpoint

Graph checkpoint는 별도 Memory reviewer model이 아니라 **같은 main model을 graph-only cognition state로 다시 호출**하는 구조다.

Checkpoint에서는 answer와 external work tool을 사용할 수 없다.
허용되는 것은 memory action 또는 explicit `sync_complete`뿐이다.

한 checkpoint LLM 호출도 여전히 **action 하나만** 낸다.

```text
Checkpoint LLM
→ memory action 1개
→ 실제 결과를 Working Graph에 반영
→ sync_complete=false면 다음 checkpoint LLM
→ 완료될 때까지 Main으로 복귀 불가
```

변경할 것이 없으면 `sync_complete` action으로 종료한다.
마지막 memory action 하나로 충분하면 그 action에 `sync_complete=true`를 붙여 별도 done 호출을 생략할 수 있다.

Framework는 문자열 비교나 topic heuristic으로 무엇을 기억할지 결정하지 않는다.
Framework는 checkpoint 시점과 허용 action, commit 경계만 강제한다.

---

# 10. Tool lazy hierarchy

작은 sLLM에게 처음부터 모든 tool schema를 주지 않는다.

External tool의 첫 level은 작게 유지한다.

```text
/file
/web
```

예:

```text
/file/tree
/file/tree/manual
/file/tree/use

/web/search
/web/market
/web/current
```

모델이 같은 Agent loop에서 사용법을 이미 알고 있다면 정확한 `/.../use` path를 바로 요청할 수 있다.

잘못된 route는 Framework가 비슷한 문자열로 교정하지 않는다.
Structured error와 valid children을 반환한다.

Memory tool은 Agent core capability이므로 external namespace 뒤에 숨기지 않는다.

---

# 11. 기존 PC / Web 기능

Memory 외 runtime은 가능한 한 유지한다.

Owner는 다음 계열을 계속 사용할 수 있어야 한다.

- file tree/search/read/CRUD/download
- document read
- image analyze
- code index/search
- terminal command
- web search
- market data
- current/latest information

Trial은 user별 독립 graph memory와 허용된 web/attachment 기능만 사용한다.

파일/OS/tool failure는 fallback으로 성공 처리하지 않는다.

---

# 12. 제거된 이전 memory 구조

- dedicated `MAI_OLLAMA_MEMORY_MODEL`
- post-answer `GraphCommitPhase`
- `continue_memory`
- 별도 final-answer 뒤 Memory-model loop
- `ScratchpadRegistry`
- `scratchpad_put`
- `scratchpad_update`
- scratchpad → durable memory promotion
- old `node_lookup` + `recall_memory` split API

---

# 13. 설치 개요

```powershell
git clone https://github.com/FoggyCaligo/MAI_MyAI_sllm.git
cd MAI_MyAI_sllm
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python run_server.py
```

Ollama 모델은 `.env.example`의 실제 reference model에 맞춰 pull한다.

---

# 14. 데이터

```text
data/graph.sqlite3
```

- semantic nodes/edges
- edge versions
- source/provenance links
- long-term Actual Graph

```text
data/chat.sqlite3
```

- raw recent/history messages
- sessions
- persistent chat jobs
- compact tool-operation history

백업 시 Mai를 정상 종료한 뒤 `data/` 전체를 백업하는 것이 가장 단순하다.

---

# 15. 실패 처리

Mai는 실패를 성공처럼 숨기지 않는다.

- no semantic string routing
- no lexical fallback for failed vector recall
- no guessed tool path
- no hidden model retry
- no silent generate→fix conversion
- no silent duplicate graph creation
- no silent Working→Actual commit failure

구조 위반은 명확한 contract error로 드러낸다.
