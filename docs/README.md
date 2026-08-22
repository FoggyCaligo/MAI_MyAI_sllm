# Mai Documentation

루트에는 프로젝트를 처음 볼 때 필요한 핵심 문서만 유지한다.

- `../README.md` — GitHub 기본 노출용 영어 README
- `../README.ko.md` — 내용 기준으로 관리하는 한국어 원본 README
- `../CONTRACT.md` — 최상위 제품/실행 계약
- `../ROADMAP.md` — 이후 작업 계획

README 내용 변경은 `README.ko.md`를 먼저 수정한 뒤 영어 `README.md`를 같은 구조와 정보량으로 동기화한다.

## 현재 memory architecture의 기준 문서

새 memory 구조의 canonical contract는 다음 문서다.

- `contracts/AGENT_GRAPH_MEMORY_CONTRACT.md`

핵심 방향:

```text
single Agent loop
→ first round mandatory vector recall
→ vector-similar candidate nodes
→ selected node + active one-hop accumulated into turn ViewedGraph
→ later recalls merge into the same ViewedGraph instead of replacing it
→ memory generate/fix commits immediately to persistent SQLite graph
→ graph mutations refresh affected ViewedGraph state
→ ViewedGraph exists only for the current turn
→ final answer requires same-Agent graph-sync confirmation
```

별도 post-answer Memory LLM, `GraphCommitPhase`, `continue_memory`, request-scoped scratchpad는 target architecture에서 사용하지 않는다.

`contracts/MEMORY_MODEL_CONTRACT.md`는 과거 dedicated-memory-model 계약이 폐기되었음을 기록하는 migration note다.
`contracts/WORKING_MEMORY_CONTRACT.md`는 scratchpad 대신 ViewedGraph가 turn working memory가 되는 현재 계약을 설명한다.

Vector recall용 embedding model은 reasoning model과 분리하며 `.env`/`.env.example`에 실제 모델 이름을 명시한다.
Reference configuration은 다음과 같다.

```env
MAI_OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```

Embedding 호출 실패 시 lexical recall로 자동 fallback하지 않는다.

## 이번 memory subsystem 재구축 순서

현재 브랜치에서 memory 구현은 기존 코드에 계속 덧붙이지 않는다.
문서 계약을 기준으로 아래 순서로 다시 구축한다.

1. 먼저 canonical contract와 관련 문서를 확정한다.
2. `main` 대비 현재 브랜치 변경 상태를 확인한다.
3. 중간 실험 과정에서 추가/변형된 memory subsystem 구현을 통째로 걷어낸다.
4. memory와 무관한 Agent/file/web/session/model/runtime 동작을 기준선으로 복원·유지한다.
5. 그 기준선 위에 새 memory subsystem을 독립적으로 다시 얹는다.
6. 새 memory는 Agent loop 내부의 first-class action으로만 연결한다.
7. 이전 post-answer memory loop나 scratchpad 코드를 helper/fallback 형태로 남겨 우회하지 않는다.
8. 테스트도 옛 memory 구조를 보존하는 방향이 아니라 새 canonical contract를 직접 검증하도록 교체한다.

새 구현에서 반드시 다시 검증할 핵심 계약은 다음과 같다.

- 매 turn 첫 memory query recall이 answer보다 먼저 발생한다.
- query recall은 embedding/vector similarity 후보 검색이다.
- candidate 검색만으로 one-hop 전체를 자동 주입하지 않는다.
- `memory/recall(node_id)`만 selected node + active one-hop을 ViewedGraph에 추가한다.
- ViewedGraph는 같은 turn 안에서 recall할수록 누적된다.
- 다음 Agent round는 누적된 ViewedGraph를 계속 볼 수 있다.
- turn 종료/실패 시 ViewedGraph만 초기화되고 persistent graph mutation은 유지된다.
- graph mutation은 매 round 실제 DB에 즉시 commit된다.
- 최종 answer 전 same-Agent graph-sync gate가 있다.
- node/edge는 generate보다 recall/reuse/fix를 우선한다.
- 새 node는 turn당 최대 10개다.
- node lifetime degree에는 제한이 없다.
- node별 edge mutation budget만 turn당 최대 10개다.
- directed ordered pair `(start_node_id, end_node_id)`의 current edge는 최대 1개다.
- reverse direction은 별개 edge다.
- disconnect는 edge 삭제 대신 weight를 0으로 만든다.
- `weight`와 `personal_relevance`는 별도 축이다.
- `personal_relevance`는 `user_centered=1.0`, `general_knowledge=0.5`이며 의미 분류는 Agent가 한다.
- concept/composite node를 지원하고 composite membership은 framework-owned structural data다.
- node/edge provenance는 source ID relational links로 유지한다.
- invalid tool route, graph scope, duplicate edge, cycle, budget, embedding/tool 오류는 실패로 드러낸다.

## Operations

- `OPERATIONS.md` — SQLite WAL/SHM, 정상 종료, 개발 DB 초기화
- `RUNTIME.md` — 런타임 상세 및 실행 환경
- `REBUILD.md` — 전체 재구축/복구 참고

## Contracts

- `contracts/AGENT_GRAPH_MEMORY_CONTRACT.md` — **현재 memory/Agent graph architecture의 canonical contract**
- `contracts/GRAPH_SOURCE_CONTRACT.md` — graph source provenance와 raw-source inspection
- `contracts/MEMORY_MODEL_CONTRACT.md` — dedicated post-answer memory model 폐기 기록 및 현재 model boundary
- `contracts/MODEL_CONTEXT_CONTRACT.md` — 최근 대화/tool context와 compaction
- `contracts/SESSION_RUNTIME_CONTRACT.md` — owner/trial, persistent session/job, working root
- `contracts/WEB_MARKET_CONTRACT.md` — web/market tool 계약
- `contracts/WORKING_MEMORY_CONTRACT.md` — turn-scoped ViewedGraph 및 attachment/tool evidence 계약
- `contracts/WORK_TOOL_CONTRACT.md` — work-tool 공통 계약
- `contracts/AGENT_STABILITY_CONTRACT.md` — 남아 있는 runtime stability 계약. 새 Agent graph contract와 충돌하는 옛 memory/review 동작은 적용하지 않는다.

## 계약 우선순위

문서 내용이 충돌할 경우 memory architecture에 대해서는 아래 순서를 따른다.

1. `AGENT_GRAPH_MEMORY_CONTRACT.md`
2. `WORKING_MEMORY_CONTRACT.md`
3. `MEMORY_MODEL_CONTRACT.md`의 현재-status 부분
4. 루트 `CONTRACT.md` / `RUNTIME.md`의 memory 관련 설명
5. 과거 구현을 설명하는 다른 문서

구현 단계에서는 이 우선순위에 맞춰 stale runtime code와 stale 문서를 제거/갱신한다.
