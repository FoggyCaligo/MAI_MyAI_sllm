# 프롬프트 접두사 캐시·메시지 시각·Verifier 비교

## 현재 동작
- llama.cpp requests explicitly set `cache_prompt: true` (in-memory KV prefix reuse; **no KV disk save/restore**). The underlying llama-server must support the option; use a pinned compatible build.
- MAI's core system contracts remain byte-stable and are assembled ahead of the conversation. The changing `current_time()` system block is removed from Main inputs; the final-answer Verifier retains its own authoritative clock source.
- Every persisted Web chat message already has `created_at REAL`. `ChatSessionStore.messages(include_timestamps=True)` exposes it while the default return shape stays unchanged.
- Prior user/assistant messages and the current user message receive a model-only `[Message created at <UTC ISO8601>]` header. Stored text remains untouched. Direct internal agent API calls that do not pass a timestamp continue to work, but the MAIRuntime web entrypoint generates a timestamp.
- The Web UI displays locale-formatted timestamps, with ISO UTC hover text. Error messages and historical messages retain their own stored timestamps.
- ChatRequest의 `verify` 기본값은 현재 서버에서 `MAI_VERIFIER_ENABLED` 설정을 따른다. 배포 `settings.yaml`은 `false`다. UI의 검증 체크박스로 **최종답변 Verifier**를 켜거나 끌 수 있다. Numeric/evidence Verifier review is skipped, but structural guards, tool execution, and background memory processing are not disabled.

## 비교 절차
1. Start one compatible llama.cpp server and the same Ornith GGUF; keep GPU/ctx and tool permissions fixed.
2. Verify that server `--cache-prompt` support is enabled (upstream default on modern builds). No `--slot-save-path` or KV persistence is needed.
3. Run the same task in fresh sessions with UI 검증 ON and OFF; alternate run order and repeat.
4. Record prompt evaluation timings, model rounds, tool execution counts, verifier events, total latency, evidence errors, and result correctness. Note background memory extraction can add post-answer compute.
5. Confirm the full server-side prompt prefix is stable across requests. Tool schema changes on tool_manual activation, context truncation, and slots used by other requests can still reduce cache reuse.

## 호환성
- No chat SQLite migration is needed: created_at exists already.
- `verify`를 생략한 기존 클라이언트는 서버의 `MAI_VERIFIER_ENABLED` 기본값을 사용하므로 현재 배포 설정에서는 검증이 꺼져 있다.
- Historical DB values remain raw and readable by older code. New UI-only timestamp display does not rewrite content.
- Previous tests that compare prior_messages exactly now need to account for the opt-in timestamp in model history.
- A model-facing timestamp describes **message creation time**, not a guarantee that the wall clock is still current many minutes later. For current time use the native time tool.

## 보안과 정확성
- Never rely on the UI checkbox for authorization; it controls answer review only.
- Some external OpenAI-compatible gateways may not accept `cache_prompt`; this flag is intended for the project's llama.cpp backend.
- Model KV reuse may vary depending on template, prompt matching, slots, and server eviction. These changes don't guarantee a speedup.
