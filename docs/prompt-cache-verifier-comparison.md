# Prompt-prefix caching, message timestamps, and verifier A/B mode

## Behavior
- llama.cpp requests explicitly set `cache_prompt: true` (in-memory KV prefix reuse; **no KV disk save/restore**). The underlying llama-server must support the option; use a pinned compatible build.
- MAI's core system contracts remain byte-stable and are assembled ahead of the conversation. The changing `current_time()` system block is removed from Main inputs; the final-answer Verifier retains its own authoritative clock source.
- Every persisted Web chat message already has `created_at REAL`. `ChatSessionStore.messages(include_timestamps=True)` exposes it while the default return shape stays unchanged.
- Prior user/assistant messages and the current user message receive a model-only `[Message created at <UTC ISO8601>]` header. Stored text remains untouched. Direct internal agent API calls that do not pass a timestamp continue to work, but the MAIRuntime web entrypoint generates a timestamp.
- The Web UI displays locale-formatted timestamps, with ISO UTC hover text. Error messages and historical messages retain their own stored timestamps.
- ChatRequest `verify: bool = True`: default behavior unchanged. The UI header checkbox switches the **final-answer Verifier** off for performance tests. Numeric/evidence Verifier review is skipped, but structural guards, tool execution, and background memory processing are not disabled.

## How to compare
1. Start one compatible llama.cpp server and the same Ornith GGUF; keep GPU/ctx and tool permissions fixed.
2. Verify that server `--cache-prompt` support is enabled (upstream default on modern builds). No `--slot-save-path` or KV persistence is needed.
3. Run the same task in fresh sessions with UI 검증 ON and OFF; alternate run order and repeat.
4. Record prompt evaluation timings, model rounds, tool execution counts, verifier events, total latency, evidence errors, and result correctness. Note background memory extraction can add post-answer compute.
5. Confirm the full server-side prompt prefix is stable across requests. Tool schema changes on tool_manual activation, context truncation, and slots used by other requests can still reduce cache reuse.

## Compatibility
- No chat SQLite migration is needed: created_at exists already.
- Existing clients that omit `verify` still get verification.
- Historical DB values remain raw and readable by older code. New UI-only timestamp display does not rewrite content.
- Previous tests that compare prior_messages exactly now need to account for the opt-in timestamp in model history.
- A model-facing timestamp describes **message creation time**, not a guarantee that the wall clock is still current many minutes later. For current time use the native time tool.

## Security and correctness
- Never rely on the UI checkbox for authorization; it controls answer review only.
- Some external OpenAI-compatible gateways may not accept `cache_prompt`; this flag is intended for the project's llama.cpp backend.
- Model KV reuse may vary depending on template, prompt matching, slots, and server eviction. These changes don't guarantee a speedup.
