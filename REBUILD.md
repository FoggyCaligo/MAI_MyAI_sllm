# Clean rebuild plan

This branch rebuilds the current MK4 direction from the smallest working core instead of copying the old backend tree.

## Why

The MACHI/MK4 implementation accumulated several generations of memory, agent, compatibility, tool-manual, fallback and migration paths. A real run also showed an anomalous ~499 second `write_memory` tool duration. The old write path does not contain a network/LLM call, so this branch does not guess at the cause or hide it with a timeout/fallback. Instead it separates each layer and measures it independently.

## Phase B: build upward from a minimal core

Current first layer:

```text
existing MK4-style UI
  -> FastAPI compatibility bridge
  -> compact Ollama structured actions
  -> mandatory model-driven recall
  -> fixed answer draft
  -> Sentence_Breaker writable-term scope
  -> structurally constrained memory write
  -> SQLite persistence
  -> done/release
```

Not copied yet:

- web research
- file read/edit tool suite beyond UI upload transport
- terminal tools
- document/image tools
- voice inference backend
- old compatibility/fallback layers
- old automatic graph-memory injection
- legacy response envelopes

These should be added one layer at a time only after the smaller layer is measured and stable.

## UI policy

The existing MK4 dark chat UI, mobile header layout, login modal, model selector, attachment control, MK5 avatar, tool log and response counters are carried forward. Backend code is not imported through the UI; the new server implements only the API surface the UI needs.

## Sentence_Breaker DB continuity

The existing Sentence_Breaker database is intentionally **not copied into Git**. Its accumulated data should remain the source of truth on the local machine.

Set either:

```env
MAI_SENTENCE_BREAKER_DB_PATH=C:/.../MACHI/MK4/data/sentence_breaker.db
```

or keep an existing:

```env
MK4_SENTENCE_BREAKER_DB_PATH=C:/.../MACHI/MK4/data/sentence_breaker.db
```

The new runtime passes that exact path to `sentence_breaker.LanguageGraph(db_path=...)`.

By default Sentence_Breaker is required. If the configured DB/package cannot be opened, startup fails visibly instead of silently switching to regex tokenization or a fresh hidden DB.

## Latency diagnostics

Every chat response includes a `diagnostics` field. The UI ignores it, but API/log inspection can distinguish:

- Ollama recall call
- SQLite recall
- Ollama answer call
- Sentence_Breaker writable-term segmentation
- each Ollama memory-commit call
- each SQLite memory write and transaction

`write_memory` itself deliberately does **not** call Sentence_Breaker. This prevents segmentation latency from being reported as DB-write latency.

## Local bootstrap

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
# edit MAI_ALLOWED_LOGIN_IDS and MAI_SENTENCE_BREAKER_DB_PATH
python -m pytest -q
python run_server.py
```

Do not merge this branch until the tests and a real local chat run pass.
