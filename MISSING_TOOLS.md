# Mai tool migration status

이 문서는 기존 `MACHI/MK4`에서 출발한 기능 중 현재 `MAI_MyAI_sllm` runtime에 연결된 것과 아직 남은 것을 구분한다.

현재 구현의 최상위 기준은 `CONTRACT.md`다. MK4 구현 방식 자체를 그대로 복제하지 않으며, 같은 tool 이름이라도 새 contract에 맞게 의미가 달라질 수 있다.

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
- `code_search` — 현재 source/filesystem을 호출 시점에 직접 literal 검색
- `code_index` — 모델이 선택한 코드 범위를 별도 JSON metadata file로 생성/교체

현재 `code_index`는 옛 MK4의 in-memory Python repository search index와 동일한 구현이 아니다.

새 contract에서 `code_index`는 다음 정보만 저장한다.

```text
path
start_line
end_line
symbol
kind
```

source code 본문을 복제하지 않으며 `code_search`의 hidden cache/ranking source로도 사용하지 않는다. `code_search`는 index 유무와 상관없이 실제 현재 source를 직접 읽는다.

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
- 옛 `code_index`의 hidden in-memory search records — 새 contract에서 사용하지 않음
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
