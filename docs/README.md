# Mai Documentation

루트에는 프로젝트를 처음 볼 때 필요한 핵심 문서만 유지한다.

- `../README.md` — 현재 프로젝트 개요, 기억 구조, tool 목록, 설치/실행/종료 방법
- `../CONTRACT.md` — 핵심 런타임/제품 계약
- `../ROADMAP.md` — MK4 parity 및 이후 작업 계획

## Operations

- `OPERATIONS.md` — SQLite WAL/SHM, 정상 종료, 개발 DB 초기화
- `RUNTIME.md` — 런타임 상세
- `REBUILD.md` — 전체 재구축/복구 참고

## Contracts

- `contracts/AGENT_STABILITY_CONTRACT.md` — action dedup, autonomy, web grounding
- `contracts/GRAPH_SOURCE_CONTRACT.md` — graph source provenance, confidence, lazy raw-source inspection
- `contracts/MODEL_CONTEXT_CONTRACT.md` — 최근 대화/tool context와 compaction
- `contracts/SESSION_RUNTIME_CONTRACT.md` — owner/trial, persistent session/job, working root
- `contracts/WEB_MARKET_CONTRACT.md` — web/market tool 계약
- `contracts/WORKING_MEMORY_CONTRACT.md` — attachment evidence와 scratchpad
- `contracts/WORK_TOOL_CONTRACT.md` — work-tool 공통 계약

과거 PR 범위 메모, migration 완료 목록, roadmap 중복 메모처럼 현재 구현을 설명하지 않는 임시 문서는 유지하지 않는다.
