# Native tool-based Windows/Linux remote worker

This implementation restores the remote-worker scope previously developed in
PR #473, **without** CodeAct or `execute_code`.

## Execution contract

1. MAI exposes only `tool_manual` in the first model round.
2. Activating a native tool exposes its original Pydantic input contract,
   extended with `is_remote: bool = false` only for Owner tools that are
   supported by the remote worker.
3. The model chooses `is_remote` per native tool call from the user's
   requested execution target, not text matching or path heuristics.
4. `false` or omission executes on the MAI host; `true` executes
   `native.invoke` on that Owner's authenticated remote worker. A missing,
   expired, disconnected, or unsupported worker fails explicitly. Never
   fall back to local execution.
5. Model-facing results are the native tool's real result, not the transport
   envelope. Remote error type and message are retained.

For example, after activating `file_read`, call that **native function**
with its validated arguments:

```json
{"path": "/home/owner/notes.txt", "is_remote": true}
```

There is **no Python CodeAct evaluator**.

## Worker coverage

The persistent worker registers the same native implementations for filesystem,
code editing/search, CSV, structured document editing, terminal, calculator,
current time, public web search/fetch, market lookup, and browser operations.
Its Chromium session manager remains alive across multiple native calls in one
remote connection.

`image_analyze` may be routed when the MAI host has a configured Ollama vision
model; the worker must also have `MAI_REMOTE_VISION_MODEL` and optionally
`MAI_REMOTE_OLLAMA_HOST`. The worker's own vision model performs inference
on paths resolved on the worker itself.

`memory_search`, `tool_result_read`, and `file_download_link` remain
on the MAI server and do not gain an `is_remote` argument. The remote
worker does not receive the server's long-term memory, per-turn result
store, or download-link token state. Trial accounts keep their original
native-only and local permission limitations.

On Linux, `terminal_run` defaults to `/bin/sh`; on Windows, it defaults
to CMD. Unsupported shell combinations fail rather than silently translating
commands.

## Installation and authentication

From the Owner remote connection panel, create an authorized session and
select the **Windows PowerShell** or **Linux shell** command. Run that command
on the machine to be controlled. The target connects outward to the MAI host
over HTTPS, polls for native tool requests, and returns authenticated results.

The Linux script requires Python 3.11+, `curl`, `unzip`, `sha256sum`, a
working `python3 -m venv`, and the system libraries needed by Playwright
Chromium. Both platforms download an authenticated, server-built source
archive and verify its SHA-256 digest before installing the worker.
`remote-requirements.txt` intentionally excludes MAI host inference,
speech, and memory dependencies.

Installations are cached by archive digest, and a dedicated completion marker
avoids treating a partially constructed virtualenv as successfully installed.
Closing the worker revokes its remote session and cleans browser sessions.
The session credentials are sensitive; do not share the launch command.

## Known limitations and validation

- No end-to-end Windows/Linux machine test has yet been run for this PR.
- Linux distribution-specific Chromium system libraries may need installing.
- Remote temporary-file cleanup is not yet integrated with MAI host's
  final-answer temporary artifact lifecycle.
- Existing tool definitions and their structural schemas are reused; failures
  must not be converted into success-shaped fallback responses.
