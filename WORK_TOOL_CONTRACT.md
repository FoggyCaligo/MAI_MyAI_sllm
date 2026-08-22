# Work Tool Contract

Mai work tools are structurally divided by execution behavior rather than by natural-language or tool-name heuristics.

## Inspection tools

Inspection tools obtain information without intentionally changing external state.

An inspection tool must provide a callable `progress_keys(result)` contract. The returned keys identify concrete structural information obtained by the successful execution. The framework accumulates those keys for the current work phase.

After an inspection execution:

- if at least one previously unseen key was returned, the tool remains available;
- if no new key was returned, the tool is removed from subsequent model schemas for that work phase;
- there is no arbitrary global round cap;
- query text, natural-language meaning, and argument-string similarity are not used to decide progress.

A tool declaring `work_kind = "inspection"` without `progress_keys()` is a contract error. For existing progress-aware tools, the presence of `progress_keys()` itself also classifies the tool as inspection.

Current inspection tools include:

- `file_tree`: returned entry kind + concrete path
- `file_search`: returned match kind + concrete path
- `file_text_search`: concrete path + matched line
- `file_read`: concrete path + actual line range
- `code_index`: concrete key-file paths
- `code_search`: concrete result-file paths
- `document_read`: concrete path + page/paragraph position
- `image_analyze`: concrete image path
- `latest_search`: result URLs
- `web_research`: result/evidence URLs
- `market_snapshot`: provider scope + provider symbol/time structural keys

This means repeated searches are still allowed while they produce new structural evidence, but a search that produces no new evidence closes itself for the remainder of that work phase.

## Action tools

Action tools intentionally create or change state, or perform an external action whose legitimate repetition cannot be inferred from inspection-result identity.

They declare `work_kind = "action"` and are not removed by inspection progress gating.

Current action tools include:

- `file_create`
- `file_update`
- `file_delete`
- `file_download_link`
- `terminal_command`
- generic `FunctionWorkTool` by default

Action failures remain visible. The framework does not silently retry, redirect, or rewrite an action into another tool.

## Successful structured action deduplication

A successful structured tool action is identified by exact JSON structure:

```text
(tool name, arguments object)
```

Once that exact action succeeds in the current turn, the framework records its structural identity. If the model later emits the exact same action again:

- the tool is **not executed a second time**;
- the agent receives an explicit tool event with `status=rejected`, `reason=duplicate_successful_action`, and `executed=false`;
- for registered work tools, that tool is removed from subsequent schemas for the rest of the turn so the model cannot loop on the same successful operation;
- for `node_lookup`, further lookup is disabled after an exact successful duplicate request;
- recalled focus IDs are removed from later `recall_memory` schemas after successful recall;
- the agent loop continues so the model can choose another valid action or answer;
- failed actions are never recorded as successful and are not converted into success.

The comparison is exact structural JSON equality only. The framework does not compare natural-language meaning, path similarity, command similarity, substrings, or inferred intent.

Example:

```text
file_create(path=A, content=X) -> success
file_create(path=A, content=X) -> rejected, not executed, file_create closes for this turn
agent -> may answer or choose another available tool
```

Before a duplicate request occurs, a different arguments object for the same action tool remains available, so multiple legitimate actions in one turn are still possible.

The framework does not automatically reinterpret a duplicate create as `file_update`, does not ignore `FileExistsError`, and does not replay a cached success result.

This contract is independent from inspection progress gating. Inspection progress asks whether new structural information was obtained; successful-action dedup asks whether an identical already-successful operation is being requested again.

## File path provenance

The existing current-turn path provenance contract remains independent from progress gating and successful-action deduplication.

Discovery and creation may establish concrete paths. Existing-file actions are only exposed for established paths, and execution re-checks the same scope. Progress gating answers a different question: whether an inspection tool is still obtaining new structural information.
