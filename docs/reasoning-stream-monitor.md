# MAI streaming reasoning diagnostics

The monitor reviews *partial* llama.cpp `reasoning_content` during normal
SSE inference. It is a deterministic text-pattern diagnostic, not a logical
or factual correctness checker. No additional LLM or verifier call is made.

```yaml
AGENT_REASONING_MONITOR_ENABLED: true
AGENT_EVIDENCE_REUSE_ENABLED: false
AGENT_PROGRESS_SUMMARY_ENABLED: false
```

The last two options above remain intentionally disabled and are independent
of the reasoning monitor. Set the monitor to `false` and restart MAI to disable
its analysis without changing model generation or streaming.

## Output (authenticated `GET /chat/jobs/{job_id}`)

`stream_progress.reasoning_diagnostics` is either `null` (disabled or
not yet evaluated), or contains:

- `status`: `insufficient_data`, `observing`,
  `repetition_suspected`, `long_unresolved`, or `output_progress`.
- `thinking_chars`: observed reasoning text characters, not token counts.
- `recent_similarity`: conservative overlap score on three bounded
  successive word-shingle windows, 0.0 to 1.0.
- `repeat_checks`: consecutive similar-window checks (two required to
  flag suspicion).
- `content_chars` and `tool_fragment_count`: output progress signals.
- `semantic_validity: not_assessed` and `action: diagnostic_only`.

`repetition_suspected` is a **heuristic** for recurring phrasing; it does
not prove circular reasoning. `long_unresolved` flags at least 12,000
reasoning characters without any answer text or tool-call fragments; it
does not imply the result is incorrect. Neither status stops generation,
truncates thoughts, or executes incomplete native-tool arguments.
The existing 500-second absolute model-request timeout still applies.

The monitor checks at most once per second while SSE is arriving, and
analyzes only the last ~8,000 characters with bounded windows. It logs a
warning when a suspicious status first appears and does **not** log
private thought content. If a timeout occurs, the audit's existing
`timeout_partial` entry includes diagnostic metadata.

The model might emit private reasoning in a provider-specific field other
than `reasoning_content`, or include internal deliberation as normal
`content`. Those cases are not semantically evaluated by the monitor.
Verifying factual validity still requires evidence or an actual review,
which would be a separate operation.

## Verify

```bash
./portable/portable-runtimes/python/python.exe -m pytest -q tests/test_reasoning_monitor.py tests/test_llamacpp_stream_diagnostics.py tests/test_chat_jobs.py
./portable/portable-runtimes/python/python.exe -m pytest -q
```

Compare log timestamps, model stream chunk counts, reasoning diagnostics,
and actual tool/final-answer outcomes. Never infer improvement from a
detector flag alone.
