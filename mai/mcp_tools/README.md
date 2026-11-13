# MAI MCP Tools

This folder owns the host-side MCP **connection manager**, **server registry /
catalog**, and **tool_manual** discovery/activation policy.

- `mcp_servers.py` discovers **enabled** servers once when MAI starts. The
  resulting catalog lives in the configured private data directory. The
  model sees an overview but no unactivated tool schemas. When
  `tool_manual` selects a tool, MAI reconnects that MCP server, verifies
  its schema, and exposes only selected tool schemas.
- `tool_manual.py` owns the model-facing initial catalog text and native
  activation schema shared by MCP and non-MCP tools.
- `mcp_memorizer/` is a **local launcher/manifest**, not a second vendored
  copy of the independent MCP_memorizer GitHub repository. It runs the
  installed `mcp-memorizer` Python dependency as a separate stdio MCP
  process. This avoids duplicate sources and makes package upgrades explicit.

To enable or disable any server, edit
`mai/mcp_tools/mcp_servers.json` and restart MAI. No uninstallation or
download is needed when toggling `enabled`. Private memory databases
remain under `mai/mcp_tools/mcp_memorizer/MAI_MEMORY/`, never here.

See `docs/mcp-lazy-loading.md` for configuration and deployment.


## 기능별 독립 MCP 서버 (schema v5)

Owner 실행에서 `MAI_MCP_CONFIG`의 `functional_mode: true`가 기본값이다.
기존 에이전트 루프·`tool_manual`·LLM/Verifier·Workspace를 수정하지 않고,
다음 서버가 독립 stdio 프로세스로 각각 `tools/list`/호출을 제공한다.

| MCP server id | 하위 도구 원본 기능 | 실행 위치 |
| --- | --- | --- |
| `mai_internet` | `web_search`, `web_fetch`, `market_data` | MAI 호스트 |
| `mai_browser` | `browser_open`, `browser_snapshot`, `browser_click`, `browser_input_text`, `browser_press_key`, `browser_scroll`, `browser_close` | MAI 호스트 |
| `mai_file` | `file_list`, `file_search`, `file_read`, `file_write`, `file_delete`, `file_move`, `file_copy` | 현재 Workspace |
| `mai_code` | `code_search`, `code_read`, `code_symbols`, `code_edit` | 현재 Workspace |
| `mai_terminal_run` | `terminal_run` | 현재 Workspace |
| `mai_docs` | `document_edit`, `csv_aggregate` | 현재 Workspace |
| `mai_utilities` | `calculator`, `current_time` | 현재 Workspace |
| `memorizer` | `memory_search` | MAI 호스트, 독립 MCP_memorizer |
| `remote` | `switch_workspace` | MAI 호스트가 소유자 워커를 검증 |
| `host` | `restart`, `file_download_link`, `tool_result_read` | MAI 호스트 내부에서 최종 실행 |

MCP 도구 이름은 `server_id__tool_name` 형식으로 모델에 등록된다.
기존 Pydantic의 *각 도구별* 입력 JSON Schema를 공개하며,
공통 JSON 문자열 인자나 `at_remote_pc`를 추가하지 않는다.
기본 Workspace는 Local이다. 원격 전환 후 `mai_file`/`mai_code`/
`mai_docs`/`mai_terminal_run`/`mai_utilities`가 기존 인증된 워커
`native.invoke`를 사용한다. 명시적 원격 실패는 로컬 자동 재실행을 금지한다.
기억, 인터넷, 브라우저, 호스트 관리는 Workspace와 무관하게 호스트 전용이다.

Startup은 활성화된 서버만 짧게 연결해 `tools/list`를 캐시에 저장한다.
요청에서 `tool_manual(tools=[...])`가 선택한 서버만 실제 작업 동안
연결·재사용하며, 활성화 시 캐시 스키마와 live 스키마를 검증한다.
`enabled: false`인 서버의 도구는 카탈로그에서 제외된다.

기존 비공개 `mcp_servers.json`은 버전별 백업을 남기고 schema v5로
업그레이드한다. 예전에 기능별 그룹이 모두 비활성화되었던 staging
설정은 `functional_mode: false`를 유지한다.
운영자의 명시적 서버 비활성화 설정은 덮어쓰지 않는다.
긴급 회귀용으로 Owner 실행에 한해 `MAI_MCP_FUNCTIONAL_TOOLS=false`를
환경변수로 지정하면 이전 Native 레지스트리를 사용한다.

