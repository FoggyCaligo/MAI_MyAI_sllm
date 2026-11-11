# MAI streaming reasoning diagnostics

The monitor reviews *partial* llama.cpp `reasoning_content` during normal
SSE inference. It is a deterministic text-pattern diagnostic, not a logical
or factual correctness checker. No additional LLM or verifier call is made.

```yaml
AGENT_REASONING_MONITOR_ENABLED: true
AGENT_EVIDENCE_REUSE_ENABLED: false
AGENT_PROGRESS_SUMMARY_ENABLED: true
```

Evidence reuse and progress summary are independent of the reasoning monitor;
these lines show the current dev defaults. Set the monitor to `false` and restart MAI to disable
its analysis without changing model generation or streaming.

## Output (authenticated `GET /chat/jobs/{job_id}`)

`stream_progress.reasoning_diagnostics` is either `null` (disabled or
not yet evaluated), or contains:

- `status`: `insufficient_data`, `observing`,
  `repetition_suspected`, `long_unresolved`, or `output_progress`.
- `thinking_chars`: observed reasoning text characters, not token counts.
- `recent_similarity`: conservative overlap score on three bounded
  successive Unicode character 5-gram windows, 0.0 to 1.0.
- `repeat_checks`: consecutive similar-window checks (two required to
  flag suspicion).
- `content_chars` and `tool_fragment_count`: output progress signals.
- `semantic_validity: not_assessed`; `action` is normally `diagnostic_only`,
  but becomes `interrupt_and_retry` when a repeated stream is interrupted.

`repetition_suspected` is a **heuristic** for recurring phrasing; it does
not prove circular reasoning. `long_unresolved` flags at least 12,000
reasoning characters without any answer text or tool-call fragments; it
does not imply the result is incorrect. `long_unresolved` is warning-only. The stream controller interrupts a still-active
`repetition_suspected` stream only after two matching checks, at least 2,400
thinking characters, and zero answer/tool fragments. It closes that single
HTTP SSE model request, drops its partial thinking and any incomplete native
tool calls, then adds a short recovery system message and starts another
model turn. Previously completed tool results are preserved. **There is no
retry count limit**, per user request. If a model repeatedly loops even on
recovery, the configured per-call timeout still applies and the automatic
recovery may repeat indefinitely until stopped by the user.

The monitor checks at most once per second while SSE is arriving, and
analyzes only the last ~8,000 characters with bounded windows. Text is
normalized with Unicode NFC and case-folding, then compared using 5-unit
character n-grams across three consecutive 240-character windows. Combining
marks and joined glyphs are grouped with their preceding character; spaces
and punctuation do not determine word boundaries. This allows the same
rules to operate on Thai, Chinese, Japanese, Korean and English without
language detection, additional model calls or tokenizers. Unicode grouping
is approximate, and language-independent pattern overlap does not imply
semantic equivalence. It logs a warning when a suspicious status first appears, and emits an
additional interruption warning for confirmed repetition. It does **not**
log private thought content for an interrupted turn. The job audit records
a `model` event with `status=RECOVERED`, `reasoning_diagnostics`, and
`partial_output_discarded=true`; only metadata is stored for that event.
If a timeout occurs, the audit's existing `timeout_partial` entry includes
diagnostic metadata.

The model might emit private reasoning in a provider-specific field other
than `reasoning_content`, or include internal deliberation as normal
`content`. Those cases are not semantically evaluated by the monitor.
Verifying factual validity still requires evidence or an actual review,
which would be a separate operation.

## Verify

```bash
./portable/portable-runtimes/python/python.exe -m pytest -q tests/test_reasoning_monitor.py tests/test_llamacpp_stream_diagnostics.py tests/test_agent_runtime.py tests/test_chat_jobs.py
./portable/portable-runtimes/python/python.exe -m pytest -q
```

Compare log timestamps, model stream chunk counts, reasoning diagnostics,
and actual tool/final-answer outcomes. Never infer improvement from a
detector flag alone.
