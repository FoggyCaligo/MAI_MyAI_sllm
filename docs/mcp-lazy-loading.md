# MAI MCP Connection Manager: enabled-only startup discovery

## Startup and toggling

MAI uses **one MCP integration branch**, `feature/mcp-lazy-manager`.
The old native `memory_search` tool has been removed. The model accesses
`memorizer__memory_search` only through the configured MCP server.

On MAI startup, enabled MCP servers are briefly connected for
`initialize` / `tools/list`. The resulting tool catalog is atomically
rebuilt and discovery connections are closed. Disabled servers are
**not started, queried, or listed**, and their former tools are removed
from the new catalog. No manual refresh, deletion, or reinstallation is
required when switching `enabled` between `true` and `false`.
A restart applies toggles. This is *catalog initialization*; actual
tool calls remain lazy: only the server selected through `tool_manual`
is reconnected, its live schema is verified, and its native schema is
then exposed to the model.

The first start automatically creates `mai/mcp_tools/mcp_servers.json` with
Memorizer and host-only MCP_remote enabled and a command pointing to MAI's own Portable Python.
The file is **never overwritten** by later starts, so settings survive.
An explicit `MAI_MCP_CONFIG` absolute path overrides that default for
advanced configurations. The resolved catalog is stored beside the
config as `mcp_catalog.json`. Both default files stay in the untracked `mai/mcp_tools/` directory; database files remain in `mai/mcp_tools/mcp_memorizer/MAI_MEMORY/`.

Example configuration:

```json
{
  "catalog_path": "mcp_catalog.json",
  "servers": [
    {
      "id": "memorizer",
      "enabled": true,
      "command": "D:/path/to/MAI/portable/portable-runtimes/python/python.exe",
      "args": ["-m", "mai.mcp_tools.mcp_memorizer.server"],
      "env": {
        "MEMORIZER_USER_ID": "{memory_user_id}",
        "MEMORIZER_DATA_DIR": "D:/path/to/MAI/mai/mcp_tools/mcp_memorizer/MAI_MEMORY"
      }
    },
    {
      "id": "experimental_server",
      "enabled": false,
      "command": "D:/path/to/other/python.exe",
      "args": ["-m", "some.other.mcp"]
    }
  ]
}
```

The model cannot change `enabled` or supply the `memory_user_id`.
MAI supplies the authenticated principal on connection. The memory
server uses the existing `mai/mcp_tools/mcp_memorizer/MAI_MEMORY/memory.sqlite3` and
`mai/mcp_tools/mcp_memorizer/MAI_MEMORY/sentence_breaker.sqlite3`. The chat database remains owned
by MAI. Do not copy private DBs into a public repository or run a second
writer against the same DB without explicit concurrency validation.

## Installation and repair after pulling

```bash
git switch feature/mcp-lazy-manager
git pull --ff-only origin feature/mcp-lazy-manager

./portable/portable-runtimes/python/python.exe -m pip install --upgrade -e .
./portable/portable-runtimes/python/python.exe -c "import mcp_memorizer, mcp; print('MCP dependency OK')"
./mai.sh start
```

An earlier Portable Python may still have an outdated Git dependency.
If the import check or startup fails after the upgrade, inspect
`pip show mcp-memorizer` and reinstall the version pinned in MAI's
`pyproject.toml`. Do **not** delete saved databases, the external
repository, or models. A package installation is required once;
toggling enabled flags never installs or redownloads packages.

## Native tool migration

The old in-process `mai/memory/tools.py` registration has been deleted.
Memory is shown to the model only as `memorizer__memory_search`, which
offers three input modes: `keyword` (optional `limit`), `limit` only
(1..50 recent memories), or `node_id` only (one-hop expansion). Invalid
selector combinations are rejected by the MCP JSON schema and server.
Long-term memory extraction after the final response remains host
controlled and does not expose a memory-save model tool.

The built-in `remote__switch_workspace(target="local"|"remote")` is provided by `mai/mcp_tools/MCP_remote/`. It controls only the current Agent run, with `local` as the initial value. The host checks authenticated worker ownership before changing state; an unavailable remote never falls back to local. Workspace-capable native tools no longer expose `at_remote_pc` and follow the selected location. Web, browser, memory and host-control tools remain host-local.

Other MAI tools remain native until migrated into separate MCP servers.
Each additional MCP tool is internally namespaced `server_id.tool_name`; the model-facing callable alias is `server_id__tool_name` (compatible with function-call name requirements).
This implementation supports stdio MCP servers and owner access only.
Startup discovery failure is reported as an error instead of silently
running with a stale catalog. Remote HTTP MCP, durable shared MCP session
pooling, and automatic plugin installation are not implemented.


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

Trial 권한의 축소된 파일 작업은 권한 격리를 위해 현행 Native
레지스트리를 그대로 사용한다. 업로드된 이미지의 모델 비전 처리는
Model Vision 입력 경로로 유지되며 MCP 명령이 아니다. 호스트의 작업
제어·작업 결과 저장은 MCP 인터페이스를 거쳐도 실제 소유권은
MAI 호스트 런타임에 남는다.
