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

WAL is kept because Mai can execute request-detached jobs and use multiple SQLite connections/threads. This gives readers and writers a safer concurrency boundary than switching the whole runtime back to rollback-journal mode merely to keep one visible file.

For inspection while Mai is running, open the main `.sqlite3` file with a SQLite-aware viewer. The viewer should read through SQLite itself so WAL contents are included. Do not infer current state from the main file bytes alone.

## Graceful shutdown

Mai is intended to run as a foreground Uvicorn process. The normal shutdown action is the interrupt signal in the terminal that owns that process:

```text
Ctrl+C
```

On Windows terminals where `Ctrl+C` is not delivered to the process, try:

```text
Ctrl+Break
```

If the shell still does not deliver an interrupt, terminate the foreground Python/Uvicorn process from the terminal/task manager. Closing the terminal window is an abrupt process termination and is not the preferred shutdown path.

A graceful ASGI shutdown runs Mai's FastAPI lifespan cleanup, which closes chat/session/job/graph SQLite connections. Once all connections to a WAL database are closed, SQLite can checkpoint/clean up the transient `-wal` / `-shm` files. Their presence while the server is alive is normal.

Do not shut the server down in the middle of an important chat job unless interruption is intended. Persisted active jobs are intentionally marked `interrupted` after a server restart rather than being guessed as successful or silently replayed.

## Resetting development data

Chat continuity and graph memory are deliberately separate:

- `chat.sqlite3`: raw conversation history, tool-operation history, sessions, persisted chat-job state.
- `graph.sqlite3`: durable semantic nodes, edges, support counts, and provenance.

During early development, if the graph schema/creation contract was previously producing duplicated semantic nodes, starting with a new `graph.sqlite3` after the fix is the cleanest validation path.

You may keep `chat.sqlite3` if you want to preserve the visible conversation history, but remember that recent raw chat context can then mention facts that are no longer present in a newly reset graph. For the cleanest end-to-end memory test, reset both databases and start a fresh conversation.

Always stop Mai before deleting or replacing SQLite database files.
