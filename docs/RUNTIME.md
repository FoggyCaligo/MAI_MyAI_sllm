# Mai Runtime

이 문서는 현재 브랜치의 **target runtime architecture**를 설명한다.
Memory 구현은 이 문서를 기준으로 기존 memory 영역을 제거한 뒤 다시 얹는다.
Memory 상세 canonical contract는 [`contracts/AGENT_GRAPH_MEMORY_CONTRACT.md`](contracts/AGENT_GRAPH_MEMORY_CONTRACT.md)다.

---

## 1. 환경 설정

```powershell
Copy-Item .env.example .env
```

Reference model configuration:

```env
MAI_OLLAMA_MODEL=gemma4:e4b
MAI_OLLAMA_EMBEDDING_MODEL=nomic-embed-text
MAI_OLLAMA_IMAGE_MODEL=gemma4:12b
MAI_OWNER_ID=owner
```

- conversation/reasoning: `MAI_OLLAMA_MODEL`
- vector memory recall: `MAI_OLLAMA_EMBEDDING_MODEL`
- image analysis: `MAI_OLLAMA_IMAGE_MODEL`

별도 reasoning용 memory model은 target runtime에 없다.
Embedding model은 vector 생성만 담당한다.

Embedding 호출 실패 시 lexical recall로 fallback하지 않는다.

---

## 2. 설치 / 실행

```powershell
python -m pip install -r requirements.txt
python run_server.py
```

기본 주소:

```text
http://127.0.0.1:8000
```

Tailscale 공개, session, persistent chat job, SQLite lifecycle 등 memory 외 runtime은 기존 구조를 유지한다.

---

## 3. Single Agent loop

```text
User
 ↓
turn init
 ↓
Agent round 1
 └─ mandatory memory/recall(query)
      ↓
      embedding vector search
      ↓
      candidate nodes
 ↓
Agent round 2+
 ├─ memory/recall(node_id)
 │    └─ selected node + active one-hop → ViewedGraph에 누적
 ├─ memory/generate/node
 ├─ memory/generate/edge
 ├─ memory/fix/node
 ├─ memory/fix/edge
 ├─ file/web/other tool
 └─ answer after graph-sync confirmation
```

각 explicit Agent round는 structured LLM request 1회다.
Model adapter가 hidden retry/review model request를 만들지 않는다.

Agent loop에 별도 post-answer memory phase는 없다.

---

## 4. Mandatory first recall

한 turn의 첫 model action은 semantic `memory/recall(query)`여야 한다.
첫 round에서는 `answer` schema를 노출하지 않는다.

Query는 Agent가 작성한다.
Framework는 user 문장에서 keyword를 추출하거나 의미를 분류해서 query를 만들지 않는다.

Recall flow:

```text
model-authored query
 ↓
embedding(query)
 ↓
node embedding similarity
 ↓
top candidate nodes
```

Candidate search만으로 candidate one-hop을 전부 context에 주입하지 않는다.
모델이 특정 `node_id`를 고른 뒤 실제 graph neighborhood를 연다.

---

## 5. Turn ViewedGraph

ViewedGraph는 turn-local in-memory state다.

```text
start turn
→ ViewedGraph = empty

recall node A
→ A + one-hop 추가

recall node B
→ 이전 graph + B + one-hop

fix/generate
→ DB 즉시 commit
→ affected viewed state refresh

end turn
→ ViewedGraph discard
```

ViewedGraph는 persistent graph의 복사본이나 별도 DB가 아니다.
그 turn에서 Agent가 실제로 열어본 node/edge의 누적 view다.

---

## 6. Persistent memory mutation

Graph mutation은 답변 후에 몰아서 하지 않는다.

```text
memory/generate or memory/fix
→ SQLite commit
→ 다음 round에서 즉시 확인 가능
→ 다음 turn에서도 recall 가능
```

잘못된 intermediate mutation이 DB에 남을 가능성은 이 구조의 의도된 trade-off다.
Agent가 이후 이해를 수정하면 `memory/fix/*`로 현재 graph를 바로 고친다.

---

## 7. Memory API

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

`memory/recall`:
- query → vector candidate search
- node_id → active one-hop open + ViewedGraph merge

`memory/generate/node`:
- relevant query recall 선행
- concept/composite
- turn당 신규 node 최대 10

`memory/generate/edge`:
- start/end 방향 edge 생성
- ordered pair당 current edge 하나

`memory/fix/node`:
- node 수정
- composite membership 변경
- duplicate merge

`memory/fix/edge`:
- relation 수정
- weight delta
- personal relevance
- source 보강
- disconnect → weight 0

---

## 8. Graph constraints

Direction은 edge endpoint로 표현한다.

```text
A -> B != B -> A
```

동일 `(start, end)`에는 current semantic edge 하나만 존재한다.
Relation wording이 달라졌다는 이유로 parallel edge를 새로 만들지 않는다.

Permanent node degree cap은 없다.

Turn budget:

```text
new nodes <= 10
edge mutations per participating node <= 10
```

Composite node도 신규 node budget에 포함한다.

---

## 9. Source / relevance / weight

Node와 edge 모두 `source_ids`를 가질 수 있다.
Source는 relational link로 저장한다.

`personal_relevance`:

```text
user_centered = 1.0
general_knowledge = 0.5
```

`weight`:
- 0.0~1.0
- fix 시 delta 적용
- 0.0은 disconnected
- normal recall은 zero-weight edge 제외

Source reliability와 personal relevance는 서로 다른 축이다.

---

## 10. Final graph-sync gate

별도 memory review LLM call을 붙이지 않는다.

같은 Agent가 answer action을 내기 전에:

```text
persistent graph / ViewedGraph가
이번 turn의 최신 durable understanding과 일치하는가?
```

를 구조적으로 확인한다.

불일치하면 memory fix/generate를 선택하고 다음 Agent round로 간다.
일치한다고 명시한 answer만 종료 가능하다.

Framework가 answer 문장과 graph 의미를 직접 비교하지 않는다.

---

## 11. External tool lazy hierarchy

External work tool은 처음부터 전체 schema를 주지 않는다.

최상위:

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

정확한 registered route만 사용한다.
Unknown route는 error + available children을 반환한다.
Framework는 typo를 추측해서 실행하지 않는다.

Memory tool은 Agent core capability라 external tool namespace처럼 숨기지 않는다.

---

## 12. File / document / image / code / terminal

Memory 외 tool behavior는 기존 계약을 유지한다.

Owner tools include:

- file tree/search/text search/read/create/update/delete/download
- document read
- image analyze
- code index/search
- terminal command

Existing-file actions는 current-turn path provenance 등 기존 structural constraint를 따른다.
OS/filesystem/tool failure는 그대로 실패한다.

---

## 13. Web / market

Web category는 계층형 route로 제공한다.

```text
/web/search
/web/market
/web/current
```

세부 provider/query contract는 [`contracts/WEB_MARKET_CONTRACT.md`](contracts/WEB_MARKET_CONTRACT.md)를 따른다.

Provider 실패 시 다른 provider로 의미적 fallback하지 않는다.

---

## 14. 제거되는 old memory runtime

구현 재정비 시 제거 대상:

- `MAI_OLLAMA_MEMORY_MODEL` reasoning orchestration
- `GraphCommitPhase`
- `continue_memory`
- post-answer memory mutation loop
- scratchpad registry/tools
- scratchpad evidence promotion path
- old `node_lookup` + `recall_memory` split API

Memory 외 runtime은 먼저 보존하고, 그 위에 새 memory subsystem을 다시 결합한다.

---

## 15. 실패 처리

Framework는 다음을 성공으로 바꾸지 않는다.

- embedding/model/schema error
- DB/ownership error
- invalid graph mutation
- duplicate directed edge generate
- budget violation
- composite cycle
- invalid route
- tool/OS/file failure

No semantic string fallback. No guessed correction. No hidden model call.
