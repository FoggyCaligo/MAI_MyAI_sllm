# Mai Documentation

루트에는 프로젝트를 처음 볼 때 필요한 핵심 문서만 유지한다.

- `../README.md` — GitHub 기본 노출용 영어 README
- `../README.ko.md` — 내용 기준으로 관리하는 한국어 원본 README
- `../CONTRACT.md` — 핵심 런타임/제품 계약
- `../ROADMAP.md` — MK4 parity 및 이후 작업 계획

README 내용 변경은 `README.ko.md`를 먼저 수정한 뒤 영어 `README.md`를 같은 구조와 정보량으로 동기화한다.

## Operations

- `OPERATIONS.md` — SQLite WAL/SHM, 정상 종료, 개발 DB 초기화
- `RUNTIME.md` — 런타임 상세
- `REBUILD.md` — 전체 재구축/복구 참고

## Contracts

- `contracts/AGENT_STABILITY_CONTRACT.md` — action dedup, autonomy, web grounding
- `contracts/GRAPH_SOURCE_CONTRACT.md` — graph source provenance, confidence, lazy raw-source inspection
- `contracts/MEMORY_MODEL_CONTRACT.md` — 대화 모델과 post-answer graph memory 모델의 분리 및 `.env` 설정
- `contracts/MODEL_CONTEXT_CONTRACT.md` — 최근 대화/tool context와 compaction
- `contracts/SESSION_RUNTIME_CONTRACT.md` — owner/trial, persistent session/job, working root
- `contracts/WEB_MARKET_CONTRACT.md` — web/market tool 계약
- `contracts/WORKING_MEMORY_CONTRACT.md` — attachment evidence와 scratchpad
- `contracts/WORK_TOOL_CONTRACT.md` — work-tool 공통 계약

과거 PR 범위 메모, 일회성 테스트 순서, migration 완료 목록, roadmap 중복 메모처럼 현재 구현을 설명하지 않는 임시 문서는 유지하지 않는다.
