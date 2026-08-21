# Mai tool migration status

이 문서는 기존 `MACHI/MK4`에서 출발한 기능 중 현재 `MAI_MyAI_sllm` runtime에 연결된 것과 아직 남은 것을 구분한다.

현재 구현의 최상위 기준은 `CONTRACT.md`다. MK4 구현을 그대로 복제하는 것이 아니라 현재 runtime contract에 맞게 재구성하되, 코드 탐색에서는 옛 MK4의 compact in-memory structural index 방식을 다시 채택한다.

## 현재 모델-visible tool

### Memory
- `node_lookup`
- `recall_memory`
- `write_memory`
- `revise_memory`
- `done` (memory phase transition action; 성공 mutation 이후에만 노출)

### File and workspace
- `file_search`
- `file_tree`
- `file_text_search`
- `file_read`
- `file_create`
- `file_update`
- `file_delete`
- `file_download_link`

### Code navigation
- `code_index` — 요청 root의 Python source를 AST로 분석해 process-local compact structural index 생성
- `code_search` — 현재 in-memory structural index에서 관련 file/symbol 검색

`code_index`는 옛 MK4의 목적과 구조를 계승한다. imports, classes/methods, function signatures, routes, registered tool names, config constants, tests 같은 repository map 정보를 만든다.

index는 현재 Python process memory에만 존재하며 별도 persistent file/DB로 저장하지 않는다. source code 전체 본문도 별도 index storage에 복제하지 않는다.

### Documents and images
- `document_read`
- `image_analyze`

### Terminal / local machine
- `terminal_command`

### Web / current information
- `latest_search` — 모델이 직접 작성한 query를 recent/news search provider에 전달
- `web_research` — 모델이 직접 작성한 objective + queries를 실행하고 public pages를 읽어 evidence package 생성
- `market_snapshot` — explicit `provider_scope`와 `operation=lookup|snapshot` 계약으로 market provider 실행

세 web/market tool의 상세 계약은 `WEB_MARKET_CONTRACT.md`를 따른다.

`web_research`는 objective에서 query를 문자열 규칙으로 자동 생성하지 않는다. 모델이 `queries` 배열을 직접 제공한다.

`market_snapshot`은 `삼성전자`, `005930`, `KOSPI` 같은 문자열을 Framework가 보고 asset 종류를 추론하지 않는다. 모델이 `provider_scope` (`kr_equity`, `global_equity`, `index`, `fx`)를 명시한다. Provider 선택은 `.env` 실행 설정이며 실패 시 다른 provider로 자동 fallback하지 않는다.

## 현재 직접 migration하지 않는 MK4 내부 기능

- `_begin_memory_commit` — 새 runtime의 phase orchestration으로 대체
- `finish_memory_commit` — mutation 성공 이후 `done` action으로 대체
- persistent code-index file/DB — 사용하지 않음; structural index는 process-local memory만 사용
- model-visible `internet_search` — 노출하지 않음; search provider는 web tool 내부 실행 helper
- model-visible `web_page_read` — 노출하지 않음; public page reader는 `web_research` 내부 실행 helper

## Guard/history events

아래 MK4 항목은 callable model tool로 직접 이식하지 않는다.

- `execution_guard`
- `autonomy_guard`
- `web_grounding_guard`
- `file_text_activation`

필요한 실행 제약은 문자열 guard가 아니라 현재 phase/schema/authorization/tool contract로 강제한다.

## 현재 상태

기존 MK4에서 우선 복원 대상으로 정한 주요 model-visible capability는 현재 runtime에 모두 연결되어 있다. 이후 작업은 실제 로컬 실행 검증, UI/도구 사용성 조정, provider 확장, dead code 정리처럼 통합 품질을 높이는 단계로 진행한다.
