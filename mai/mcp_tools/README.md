# MAI 기능별 MCP 도구

이 폴더는 **현재 사용 중인 기능별 MCP 서버**와 `tool_manual` 도구 검색·활성화를 관리한다. 장기기억은 별도 MCP가 아니라 **네이티브 `mai/memory/`의 `memory_search`**가 담당한다.

| MCP 서버 | 공개 도구 | 실행 위치 |
| --- | --- | --- |
| `mai_internet` | `web_search`, `web_fetch`, `market_data` | Host |
| `mai_browser` | `browser_*` | Host |
| `mai_file` | `file_list`, `file_search`, `document_read`, `file_write`, `file_delete`, `file_move`, `file_copy` | Host/Client |
| `mai_code` | `code_search`, `code_read`, `code_symbols`, `code_edit` | Host/Client |
| `mai_terminal_run` | `terminal_run` | Host/Client |
| `mai_docs` | `document_edit`, `csv_aggregate` | Host/Client |
| `mai_utilities` | `calculator`, `current_time` | Host/Client |
| `remote` | `switch_workspace` | Host |
| `host` | `restart`, `file_download_link`, `tool_result_read` | Host |

- Owner는 기본 `functional_mode: true`로 기능별 MCP를 사용한다. Trial 권한은 제한된 네이티브 도구를 사용한다.
- 새 작업의 도구 실행 대상은 `host`. `remote__switch_workspace(target="client")` 성공 뒤에는 호환 도구가 해당 작업 동안 Client에서 실행된다. 연결 실패 시 Host로 자동 우회하지 않는다.
- MCP 서버는 시작 시 활성 항목만 발견하고, 모델이 `tool_manual`로 선택한 도구만 작업 중 연결한다. 활성화 시 JSON Schema를 다시 검증한다.
- 비공개 설정 파일은 `mai/mcp_tools/mcp_servers.json`. 기본 설정은 사용자가 변경한 enabled 값을 덮어쓰지 않으며, 구 `memorizer` 항목은 마이그레이션에서 제거한다.
- 과거 `mai/mcp_tools/mcp_memorizer/MAI_MEMORY/`에 저장된 **사용자 DB는 그대로 유지한다**. 디렉터리 이름은 데이터 호환을 위한 잔존 경로로서 삭제하면 안 된다.

예제: [mcp_servers.example.json](mcp_servers.example.json) · [상세 MCP 연결·전환](../../docs/mcp-lazy-loading.md) · [네이티브 기억](../../docs/memory.md).
