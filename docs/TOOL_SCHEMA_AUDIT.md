# Work Tool Schema Audit

This audit covers every owner work tool currently assembled by the Mai runtime after the `market_snapshot` lazy-manual schema fix.

## Audited tool set

The owner runtime exposes 18 work tools through the lazy tool catalog:

- file: `file_tree`, `file_search`, `file_text_search`, `file_read`
- file mutation: `file_create`, `file_update`, `file_delete`, `file_download_link`
- document/image: `document_read`, `image_analyze`
- terminal: `terminal_command`
- code: `code_index`, `code_search`
- web/market: `latest_search`, `web_research`, `market_snapshot`
- scratchpad: `scratchpad_put`, `scratchpad_update`

## Common lazy-manual contract

Every work tool must expose a top-level structured action object with:

```text
object
  action = tool
  tool = <tool.name>
  arguments = <tool-specific input schema>
```

`tool_manual` depends on this structure to expose only the selected tool's `arguments` schema.

## Findings

The full audit found one existing contract violation: `market_snapshot` placed its `lookup`/`snapshot` union at the top level instead of under `arguments`. That caused `tool_manual` to fail with `KeyError: 'properties'`. This was fixed in PR #52 by preserving the common envelope and moving the operation union under `arguments.oneOf`.

No additional current work tool was found to violate the same schema contract.

## Regression coverage

`tests/test_all_work_tool_schema_contracts.py` now verifies:

1. the exact current set of 18 owner work tools,
2. the common lazy-manual envelope for every base schema,
3. the same envelope for every current context-dependent `schema_for_paths()` result,
4. an actual `tool_manual -> answer` agent loop for every registered work tool,
5. that `market_snapshot` keeps both `lookup` and `snapshot` under `arguments.oneOf`.

The tests use fake web/market/image providers and do not perform network access or execute the work tools themselves.
