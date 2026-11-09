# MAI deterministic long-task efficiency

This feature has **zero additional LLM calls**. It reuses existing agent tool
outputs and computes progress metadata from execution records in Python.

Configure in `settings.yaml` (restart MAI after changing):

```yaml
AGENT_EVIDENCE_REUSE_ENABLED: true
AGENT_PROGRESS_SUMMARY_ENABLED: true
```

## 1. Round timing

Every successful model call emits `MAI model timing round=N model_elapsed_ms=X`
through the server logger. At the end of a tool round, successful answer, or
verifier retry it emits `MAI round timing` with:
- `round`: current agent round number.
- `total_elapsed_ms`: elapsed wall-clock time in that round.
- `model_elapsed_ms`: one main-model request's latency.
- `tool_elapsed_ms`: summed synchronous tool-handler time in that round.
- `verifier_elapsed_ms`: final verifier duration, where applicable.
- `status`: `tools`, `completed`, `verification_retry`,
  `empty_final_retry`, or `failed`.

The failure event includes a round's total elapsed time even when a model
request times out before producing a turn. These are elapsed-time diagnostics,
not token counts or model GPU utilization.

## 2. Read-result reuse

With evidence reuse enabled, an **identical successful** `web_search`,
`web_fetch`, `file_read` or `code_read` call in the same agent run
reuses the exact earlier tool output. A run ends with its cache; no data
is shared across users, sessions, or independent prompts.

Any `file_write`, `document_edit`, `code_write` or `terminal_run` call
clears the read cache because it may change filesystem state. Cache hits
are logged as `MAI read cache hit`. Errors and mutating tools are never
cached. Outside changes to files or web pages during an active run cannot be
detected; disable reuse for tasks that require rechecking live state.

## 3. Deterministic progress summary

When enabled, after at least three tool executions the model receives a
bounded, **non-semantic** progress ledger containing recent distinct tool
names, success/failure types, evidence SHA-256 hashes, and source URLs or
paths when explicit in tool arguments. It is not an AI-generated conclusion:
no factual claims may be inferred from the ledger without reading the
associated original tool results.

Previous web results already compact to literal excerpts. Large file/code
reads now do likewise after their first model-visible turn. Full original
text remains available via `tool_result_read` using the original result
ID; source content used for audit is not overwritten.

With `AGENT_PROGRESS_SUMMARY_ENABLED: false`, file/code history
compaction and the progress ledger are disabled, independently of
the evidence-reuse toggle. Existing web-result paging remains.

## 4. Related failure notices

The structural `AgentGuard` also groups repeated failures for limited
tool/target families based on error type and conservative tool metadata.
After two related failures it gives the model a warning recommending a
different approach. It does **not** block changed tool calls, guess that
different commands are equivalent, or generate a summary with an LLM.
The pre-existing identical-call guard remains independent.

## Verification

```bash
./portable/portable-runtimes/python/python.exe -m pytest -q tests/test_agent_deterministic_efficiency.py tests/test_agent_guards.py
./portable/portable-runtimes/python/python.exe -m pytest -q
```

Compare identical GAIA validation tasks for final accuracy, model rounds,
tool executions, repeated failures, and total runtime. Fewer tool calls alone
are not proof of improvement.
