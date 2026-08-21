# MK4 tools not yet migrated

This list is based on the tool suites actually registered by `MACHI/MK4/app/pipeline.py` on current `main`.

The minimal MAI rebuild currently implements only:

- `recall_memory`
- `write_memory`

Everything below is intentionally deferred so each capability can be added and measured one layer at a time.

## Model-visible tools still missing

### Memory
- `revise_memory` — connect/update/replace in-scope semantic memory
- `tool_manual` — lazy full tool description/schema lookup

### File and workspace
- `file_search` — filename/glob search
- `file_tree` — directory tree inspection
- `file_text_search` — text search with paths/line numbers
- `file_read` — UTF-8 text read
- `file_create` — create text files
- `file_update` — append/replace/overwrite text files
- `file_delete` — delete files
- `file_download_link` — user-facing temporary download URL

### Code navigation
- `code_index` — build compact Python repository map
- `code_search` — search the compact code index

### Documents and images
- `document_read` — PDF/DOCX text extraction
- `image_analyze` — image metadata + configured Ollama vision analysis

### Terminal / local machine
- `terminal_command` — inspect/modify local system state with explicit precondition and verification contracts

### Web / current information
- `latest_search` — freshness-sensitive news/web search
- `web_research` — multi-query research + page reading + compact evidence package
- `market_snapshot` — Korean/global stocks, indices, and FX snapshot

## MK4 runtime/internal tools not migrated

These exist in MK4 but are not normal user-facing capabilities and should not be treated as equal priority with the model-visible tools above.

- `_begin_memory_commit` — framework-only phase transition
- `finish_memory_commit` — memory-commit completion transition (the new rebuild currently represents completion directly as `done`)
- `internet_search` — internal web-search primitive hidden from the model
- `web_page_read` — internal page-reading primitive hidden from the model

## Guard/history events not callable tools

MK4 also writes structural guard events into tool history. They are not tool capabilities to migrate directly:

- `execution_guard`
- `autonomy_guard`
- `web_grounding_guard`
- `file_text_activation`

## Suggested restoration order

1. `revise_memory`
2. `tool_manual`
3. file discovery/read (`file_tree`, `file_search`, `file_text_search`, `file_read`)
4. file mutation (`file_create`, `file_update`, `file_delete`, `file_download_link`)
5. `terminal_command`
6. `document_read`, `image_analyze`
7. `latest_search`, `web_research`, `market_snapshot`
8. `code_index`, `code_search`

The order is intentionally conservative: keep the core measurable and add one capability family at a time.
