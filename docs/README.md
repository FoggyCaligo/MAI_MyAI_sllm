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
→ candidate node selection
→ selected node + one-hop accumulated into turn ViewedGraph
→ memory generate/fix commits immediately to persistent SQLite graph
→ ViewedGraph persists only for the current turn
→ final answer requires same-Agent graph-sync confirmation
```

별도 post-answer Memory LLM, `GraphCommitPhase`, `continue_memory`, request-scoped scratchpad는 target architecture에서 사용하지 않는다.

`contracts/MEMORY_MODEL_CONTRACT.md`는 과거 dedicated-memory-model 계약이 폐기되었음을 기록하는 migration note다.
`contracts/WORKING_MEMORY_CONTRACT.md`는 scratchpad 대신 ViewedGraph가 turn working memory가 되는 현재 계약을 설명한다.

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
