# Mai

[English](README.md) | **한국어**

> README 유지보수 원칙: `README.ko.md`를 내용 원본으로 관리하고 영어 README를 같은 구조로 동기화한다.

Mai는 **로컬 sLLM에 장기 graph memory와 실제 PC/web 도구를 붙여 개인용 semi-GPT를 만드는 프로젝트**다.

핵심 철학은 다음과 같다.

> **모델이 의미를 결정하고, Framework는 구조를 강제한다.**

현재 브랜치는 새 memory architecture를 문서로 먼저 확정한 뒤 구현을 재구축하는 단계다.
구현은 기존 memory 영역을 통째로 걷어내고, memory 이외의 runtime을 최대한 유지한 상태에서 새 memory subsystem을 다시 얹는 방식으로 진행한다.

Memory 상세 기준 문서:

- [`docs/contracts/AGENT_GRAPH_MEMORY_CONTRACT.md`](docs/contracts/AGENT_GRAPH_MEMORY_CONTRACT.md)

---

# 1. 목표 구조

```text
사용자
 ↓
최근 대화 context
 ↓
Mai single Agent loop
 ├─ mandatory vector memory recall
 ├─ turn ViewedGraph
 ├─ persistent graph generate/fix
 ├─ file / document / image / code / terminal
 ├─ web / market / current info
 └─ answer
```

별도 post-answer Memory LLM은 사용하지 않는다.
Scratchpad도 target architecture에서는 제거한다.

Graph는 동시에 두 역할을 가진다.

1. 여러 turn과 재실행을 넘어 유지되는 long-term memory
2. Agent가 현재 turn 동안 직접 조회/수정하는 working-memory substrate

---

# 2. 한 turn의 memory 흐름

첫 Agent round에서는 바로 답변할 수 없다.
최소 한 번 `memory/recall(query)`를 사용해야 한다.

```text
User message
 ↓
Agent round 1
 ↓
memory/recall(query)
 ↓
Embedding vector similarity search
 ↓
관련 node 후보 여러 개
 ↓
Agent가 필요한 node_id 선택
 ↓
memory/recall(node_id)
 ↓
선택 node + active one-hop
 ↓
ViewedGraph에 누적
```

이후 다른 node를 recall하면 이전 조회 결과를 지우지 않는다.

```text
ViewedGraph(next)
= ViewedGraph(current)
+ 새로 조회한 node/edge
```

Turn이 끝나면 ViewedGraph 자체는 초기화된다.
Persistent graph DB에 commit된 memory는 남는다.

---

# 3. Vector recall

`memory/recall(query)`는 lexical substring 검색이 아니라 embedding vector similarity로 candidate node를 찾는 것을 목표로 한다.

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

두 mode를 가진다.

- `query`: vector-similar node 후보 조회
- `node_id`: 특정 node와 active one-hop 열기

## `memory/generate/node`

새 semantic node 또는 composite node를 만든다.
새 node 생성 전에는 relevant query recall이 선행되어야 한다.
모델이 후보 중 동일 의미 node가 있다고 판단하면 새로 만들지 않고 기존 node를 재사용해야 한다.

한 turn 신규 node 상한은 **10개**다.
Composite도 동일 budget을 사용한다.

## `memory/generate/edge`

Edge 방향은 start/end로 표현한다.

```text
A -> B
B -> A
```

는 서로 다른 edge다.

동일 `(start_node_id, end_node_id)`에는 current semantic edge 하나만 존재한다.
Relation wording이 달라졌다는 이유로 parallel edge를 추가하지 않는다.

## `memory/fix/node`

- node 이름/상태 수정
- composite membership 수정
- duplicate node merge

## `memory/fix/edge`

- 현재 relation 수정
- `weight_delta` 반영
- personal relevance 갱신
- source 추가
- disconnect

Disconnect는 edge를 지우는 대신 weight를 `0`으로 만든다.
Zero-weight edge는 provenance/debug에는 남지만 normal recall에서는 active connection으로 보지 않는다.

---

# 5. Node / Edge 구조

## Node

```text
node_id
name
kind: concept | composite
source_ids[]
```

Composite node는 여러 기존 node를 하나의 새 개념으로 지칭한다.
Membership은 일반 relation 문자열이 아니라 Framework-owned structural data다.
Self-membership과 membership cycle은 허용하지 않는다.

## Edge

```text
edge_id
start_node_id
end_node_id
relation
weight
personal_relevance
source_ids[]
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

# 7. Source / provenance

Node와 edge 모두 source sentence/tool evidence의 ID를 독립적으로 가질 수 있다.

모델에게는:

```text
source_ids: [12, 18, 44]
```

형태로 보여줄 수 있다.

DB는 JSON list 하나에 묻지 않고 relational source/link table로 관리한다.

과거 edge를 3겹으로 쌓는 history 구조는 사용하지 않는다.
현재 graph는 현재 이해를 표현하고, provenance가 그 상태의 근거를 보존한다.

---

# 8. Graph mutation은 즉시 commit

Memory는 최종 답변 뒤에 따로 저장하지 않는다.

```text
Agent round N
→ memory/generate or memory/fix
→ SQLite 즉시 commit

Agent round N+1
→ 변경된 graph를 바로 recall 가능
```

미래 turn에서도 동일 graph를 다시 볼 수 있다.

이 때문에 intermediate 판단이 잘못된 상태로 commit될 수 있다.
새 구조에서는 이를 숨기거나 rollback하기보다, Agent가 이해를 수정했을 때 `memory/fix/*`로 현재 graph를 다시 맞추는 것을 기본으로 한다.

---

# 9. Final graph-sync gate

별도 Memory reviewer 모델을 다시 붙이지 않는다.

같은 Agent loop의 마지막 answer 전에:

> 현재 persistent graph / ViewedGraph가 이번 turn에서 얻은 최신 durable understanding과 일치하는가?

를 Agent가 명시적으로 확인한다.

일치하지 않으면 memory fix/generate를 수행하고 다음 round로 진행한다.
일치한다고 확인된 상태에서만 answer로 종료한다.

Framework가 answer text와 graph 의미를 문자열 비교하지 않는다.

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

# 12. 제거할 이전 memory 구조

새 memory 구현 단계에서 제거 대상:

- dedicated `MAI_OLLAMA_MEMORY_MODEL`
- post-answer `GraphCommitPhase`
- `continue_memory`
- final-answer 뒤 memory loop
- `ScratchpadRegistry`
- `scratchpad_put`
- `scratchpad_update`
- scratchpad → durable memory promotion
- old `node_lookup` + `recall_memory` split API

Memory 외 runtime은 먼저 보존한 뒤 새 memory 구조를 덧입힌다.

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
- graph state
- source/provenance links
- long-term memory

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

구조 위반은 명확한 contract error로 드러낸다.
