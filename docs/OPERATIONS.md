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

## Resetting development data

Chat continuity and graph memory are deliberately separate:

- `chat.sqlite3`: raw conversation history, tool-operation history, sessions, persisted chat-job state.
- `graph.sqlite3`: durable semantic nodes, edges, support counts, and provenance.

During early development, if the graph schema/creation contract was previously producing duplicated semantic nodes, starting with a new `graph.sqlite3` after the fix is the cleanest validation path.

You may keep `chat.sqlite3` if you want to preserve visible conversation history, but recent raw chat context can then mention facts that are no longer present in a newly reset graph. For the cleanest end-to-end memory test, reset both databases and start a fresh conversation.

Always stop Mai before deleting or replacing SQLite database files.