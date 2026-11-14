# MAI 기능별 MCP 구성·Lazy Activation

현재 `dev`는 **기능별 MCP 도구**와 **네이티브 장기기억**을 병행한다. `memory_search`는 `mai/memory/`에서 Host 전용으로 등록하며, 이전의 `memorizer__memory_search` MCP 서버는 사용하지 않는다.

## 기능별 MCP 도구

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

각 MCP 도구는 모델에 `server_id__tool_name`으로 공개되고, 원본 Pydantic 입력 스키마는 유지된다. `tool_manual`에서 선택한 도구만 사용 가능하다. 초기 연결에서 활성 서버의 `tools/list` 결과를 캐시하고, 실제 활성화할 때 서버의 최신 스키마와 비교한다. `enabled: false`인 서버의 도구는 노출하지 않는다.

## 기본 설정

설정 파일: `mai/mcp_tools/mcp_servers.json` (사용자별 비공개·Git 무추적). [실제 예제](../mai/mcp_tools/mcp_servers.example.json)를 참고한다. 처음 실행 시 schema-v5 설정을 생성하고 기존 설정은 백업 후 마이그레이션하며 사용자의 서버별 활성화 선택을 유지한다. 예전 `memorizer` 항목은 제거한다.

Owner의 기본값은 `functional_mode: true`. 문제가 있으면 `MAI_MCP_FUNCTIONAL_TOOLS=false`로 기존 기능 도구를 네이티브로 실행할 수 있다. 이 변경은 **기억 엔진**을 바꾸지 않는다. Trial은 권한 제한된 네이티브 도구를 사용한다.

## Host/Client 실행 위치

모든 새 사용자 작업은 `host`에서 시작한다. `remote__switch_workspace(target="client")`가 성공하면 이후 호환 도구(`mai_file`, `mai_code`, `mai_docs`, `mai_terminal_run`, `mai_utilities`)는 해당 작업이 끝나거나 다시 전환할 때까지 Client에서 실행한다. 브라우저·인터넷·기억·호스트 제어는 항상 Host에서 처리한다.

연결되지 않은 Client로 전환하지 않으며, Client 실행 실패를 Host에서 자동 재시도하지 않는다. Client 임시 파일 생성은 원격 정리가 구현되기 전까지 명시적으로 거부한다. 메모리 DB는 기존 `mai/mcp_tools/mcp_memorizer/MAI_MEMORY/` 경로를 유지하며 **이 디렉터리를 삭제하지 않는다**.

## 도구 실패 의미

MCP 연결 실패와 서버 도구 실행 실패는 구분된다. 모델에 노출되는 도구 실패는 `ok: false`, 실제 예외명 `error_type`, 공통 `error_category`, `retryable`, `message`를 포함한다. `retryable`은 재시도가 안전하게 성공한다는 보장이 아니라, 같은 실패를 영구 차단하지 않는다는 힌트다.

원본: [MCP 연결 관리자](../mai/mcp_tools/mcp_servers.py) · [오류 계약](tool-error-contract.md) · [기억 정책](memory.md).
