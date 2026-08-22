# Mai Operations

## SQLite files

Mai uses SQLite in WAL mode for both chat/session state and the semantic graph.

The primary durable databases are:

- `chat.sqlite3`
- `graph.sqlite3`

While Mai is running, SQLite may also create:

- `*.sqlite3-wal`: committed/new pages that have not yet been checkpointed back into the main database file.
- `*.sqlite3-shm`: shared-memory coordination metadata used by WAL readers/writers.

These files are part of one live SQLite database state. Do not copy, delete, or edit only one member of a live WAL set while Mai is running.

WAL is kept because Mai can execute request-detached jobs and use multiple SQLite connections/threads. This gives readers and writers a safer concurrency boundary than switching the runtime back to rollback-journal mode merely to keep one visible file.

For inspection while Mai is running, open the main `.sqlite3` file with a SQLite-aware viewer. The viewer should read through SQLite itself so WAL contents are included. Do not infer current state from the main file bytes alone.

## Graceful shutdown

### Local launcher

When Mai is started with:

```powershell
python run_server.py
```

it runs as the foreground Uvicorn process. Press:

```text
Ctrl+C
```

in the same terminal. On Windows terminals where `Ctrl+C` is not delivered, `Ctrl+Break` can also be tried.

### Tailscale launcher

The current `start_public_tailscale.cmd` / `start_public_tailscale.ps1` launcher also keeps the Python server in the same terminal foreground. Press `Ctrl+C` in that launcher window to stop Mai normally.

Older launcher versions used `Start-Process`, which could leave the Python server alive after the PowerShell launcher returned. If an older orphaned server is already listening on port 8000, stop it once with:

```powershell
$pid = (Get-NetTCPConnection -LocalPort 8000 -State Listen).OwningProcess
Stop-Process -Id $pid
```

Then restart with the current launcher.

A graceful ASGI shutdown runs Mai's FastAPI lifespan cleanup, which closes chat/session/job/graph SQLite connections. Once all connections to a WAL database are closed, SQLite can checkpoint/clean up transient `-wal` / `-shm` files.

Do not shut the server down in the middle of an important chat job unless interruption is intended. Persisted active jobs are intentionally marked `interrupted` after a server restart rather than being guessed as successful or silently replayed.

## Resetting the graph for this memory revision

Chat continuity and graph memory are deliberately separate:

- `chat.sqlite3`: raw conversation history, tool-operation history, sessions, and persisted chat-job state.
- `graph.sqlite3`: current semantic nodes/edges, node embeddings, composite membership, and durable source links.

The live-Agent graph-memory revision intentionally does **not** migrate the retired post-answer memory schema. The graph repository stores a schema-version marker and fails visibly when it sees an older graph database instead of guessing how to reinterpret it.

Before first running this revision against an existing development checkout:

1. stop Mai completely;
2. keep `chat.sqlite3` if visible conversation/session history should remain;
3. delete only the graph database WAL set;
4. restart Mai and let it create the new graph schema.

With the default paths on PowerShell:

```powershell
Remove-Item data/graph.sqlite3 -ErrorAction SilentlyContinue
Remove-Item data/graph.sqlite3-wal -ErrorAction SilentlyContinue
Remove-Item data/graph.sqlite3-shm -ErrorAction SilentlyContinue
```

Do **not** delete `data/chat.sqlite3` for this schema reset unless chat/session/job history should also be erased.

If `MAI_GRAPH_DB` points somewhere else, apply the same reset to that configured graph path and its `-wal` / `-shm` companions.

After a graph-only reset, recent raw chat context can still mention facts that have not yet been rebuilt into the new graph. That difference is expected: raw chat history and semantic graph memory are separate stores.

Always stop Mai before deleting or replacing SQLite database files.
