# Clean rebuild plan

This branch rebuilds the current MK4 direction from the smallest working core instead of copying the old backend tree.

## Why

The MACHI/MK4 implementation accumulated several generations of memory, agent, compatibility, tool-manual, fallback and migration paths. A real run also showed an anomalous ~499 second `write_memory` tool duration. The old write path does not contain a network/LLM call, so this branch does not guess at the cause or hide it with a timeout. Instead it separates each layer and measures it independently.

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
- old compatibility layers
- old automatic graph-memory injection
- legacy response envelopes

See `MISSING_TOOLS.md` for the concrete MK4 tool inventory and restoration order.

## UI policy

The existing MK4 dark chat UI, mobile header layout, login modal, model selector, attachment control, MK5 avatar, tool log and response counters are carried forward. Backend code is not imported through the UI; the new server implements only the API surface the UI needs.

## Sentence_Breaker DB continuity and fallback

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

Fallback is enabled by default:

```env
MAI_SENTENCE_BREAKER_FALLBACK=true
```

If Sentence_Breaker cannot open the configured DB/package, or segmentation later fails, the runtime switches to a small Unicode regex tokenizer. This fallback is **not silent**:

- `/health` reports `sentence_breaker_mode=fallback` and the reason;
- startup prints one compact fallback line;
- each chat timing summary labels segmentation as `segment.fallback`.

Set `MAI_SENTENCE_BREAKER_FALLBACK=false` when a Sentence_Breaker failure must stop startup/the turn instead.

## Human-readable latency logs

Detailed timing still exists in the chat response `diagnostics` field for debugging, but normal console output is intentionally small.

A normal turn looks roughly like:

```text
[MAI] chat | model.recall=1.20s | db.recall=0.4ms | model.answer=2.31s | segment.sentence_breaker=3.0ms | model.memory1=1.05s | db.write1=0.8ms | model.memory2=0.72s
```

If fallback is active, the same line says `segment.fallback=...`.

`write_memory` deliberately does **not** call Sentence_Breaker, so segmentation latency cannot be misreported as DB-write latency.

Uvicorn access logging is disabled in `run_server.py`; warnings/errors remain visible.

## Tailscale public hosting

Windows launchers are included:

```powershell
.\start_public_tailscale.ps1
```

or:

```cmd
start_public_tailscale.cmd
```

The launcher:

1. verifies `python` and `tailscale` are available;
2. keeps MAI bound to `127.0.0.1`;
3. enables secure session cookies for the public HTTPS endpoint;
4. runs `tailscale funnel --bg <port>`;
5. shows `tailscale funnel status`;
6. starts `run_server.py`.

Tailscale/Funnel command failures are not hidden; the launcher stops with an error.

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
