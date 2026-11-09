# MAI llama.cpp streaming inference

All MAI `/v1/chat/completions` requests now use `stream: true`
and consume llama.cpp Server-Sent Events (SSE), including the
request-detached `/chat/jobs/` path used by GAIA.

## Behavior

- Stream chunks are assembled into one complete assistant turn.
- Native tool-call argument fragments are **not executable** until the
  server sends a finish reason and `[DONE]`. A dropped stream is an error.
- `MODEL_REQUEST_TIMEOUT_SECONDS` remains an **absolute per-request**
  limit (currently configured at 500 seconds). Continuous token output
  cannot extend it indefinitely.
- Every active job's status now includes `stream_progress`, with
  `round`, `chunks`, `content`, `finish_reason`, `complete`, and
  `tool_call_fragments`. The existing `thinking` and `model_round`
  values also update as chunks arrive. The API continues to require
  authenticated access to the job.
- On a model timeout, partial thinking/content and incomplete tool
  fragments are recorded in the audit log with `timeout_partial`
  status. They are **never** accepted as a completed answer or tool call.
- The server logs first and periodic stream progress with token-related
  *character counts* only, not private response text. Look for
  `MAI llama.cpp stream progress chunks=...`.
- Existing cached prompt handling, router model transitions, memory
  guard and `SESSION_HISTORY_MESSAGES` behavior are unchanged.

## Troubleshooting a GPU-busy apparent hang

First inspect `GET /chat/jobs/<job-id>` for `stream_progress`.
If `chunks` increases, the llama.cpp stream is arriving and the model
is generating; longer thinking or output is not a transport stall. If
no chunks appear before the 500-second timeout, examine the
llama.cpp 8081 process's logs, memory load, context evaluation, and
the 8080 gateway queue rather than assuming the agent ran another tool.

Streaming does not speed up the model or change its reasoning policy.
The two deterministic efficiency options are independent:
`AGENT_EVIDENCE_REUSE_ENABLED` and
`AGENT_PROGRESS_SUMMARY_ENABLED`. Their current values in
`settings.yaml` are intentionally not changed by this feature.

## Tests

```bash
./portable/portable-runtimes/python/python.exe -m pytest -q tests/test_llamacpp_stream_diagnostics.py tests/test_llamacpp_adapter.py tests/test_chat_jobs.py
./portable/portable-runtimes/python/python.exe -m pytest -q
```
