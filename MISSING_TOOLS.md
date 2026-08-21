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

차이는 실패를 fallback으로 숨기지 않는 현재 runtime contract를 따른다는 점이다. index는 현재 Python process memory에만 존재하며 별도 persistent file/DB로 저장하지 않는다. source code 전체 본문도 별도 index storage에 복제하지 않는다.

`code_search`는 index가 없거나 요청 root가 달라지면 현재 source로 index를 자동 생성한다. 같은 root의 source 변경은 자동 rebuild하지 않으므로, 최신 구조가 필요하면 모델이 `code_index`를 다시 호출한다. 실제 상세 source는 이후 `file_read`로 읽는다.

### Documents and images
- `document_read`
- `image_analyze`

### Terminal / local machine
- `terminal_command`

## 아직 구현하지 않은 model-visible capability

### Web / current information
- `latest_search`
- `web_research`
- `market_snapshot`

이 세 이름은 MK4에서 가져온 후보이며, 새 runtime에서 그대로 유지할지/통합할지는 web tool 구현 단계의 contract에 따라 결정한다.

## MK4 runtime/internal 기능 중 직접 migration하지 않는 것

- `_begin_memory_commit` — 새 runtime의 phase orchestration으로 대체
- `finish_memory_commit` — mutation 성공 이후 `done` action으로 대체
- persistent code-index file/DB — 사용하지 않음; structural index는 process-local memory만 사용
- `internet_search` / `web_page_read` — web capability 구현 전까지 미연결

## Guard/history events

아래 MK4 항목은 callable model tool로 직접 이식하지 않는다.

- `execution_guard`
- `autonomy_guard`
- `web_grounding_guard`
- `file_text_activation`

필요한 실행 제약은 문자열 guard가 아니라 현재 phase/schema/authorization/tool contract로 강제한다.

## 다음 단계

현재 순서상 다음 구현 대상은 web/current-information capability다.
