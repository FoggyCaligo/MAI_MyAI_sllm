# Work Tool Contract

Mai work tools are structurally divided by execution behavior rather than by natural-language or tool-name heuristics.

## Deferred tool manuals

Mai follows the MK4 deferred-tool pattern: the model does not receive every work tool's full JSON schema on the first agent round.

The agent always receives:

- the final `answer` schema;
- built-in memory actions (`node_lookup`, and `recall_memory` when candidates exist);
- `tool_manual`;
- a compact catalog containing each registered work tool's name and short purpose.

A registered work tool's full action schema is exposed only after the model calls:

```text
tool_manual(tool=<registered tool name>)
```

The manual result contains the selected tool's full description and argument schema. From the following round onward, that tool may be exposed as an executable action while it remains available under the other framework contracts.

`tool_manual` activation does not bypass inspection progress gating, ownership, or any other execution contract. For mutating file actions such as `file_update`, it also does not bypass path provenance.

User-facing conversational text must be delivered only through the final `answer` action. Work tools are not answer-delivery channels.

The framework does not inspect the user's natural-language text to decide which tool to activate. The model chooses whether to read a tool manual.

## Inspection tools

Inspection tools obtain information without intentionally changing external state.

An inspection tool may target a concrete existing path directly after its manual is activated. It does not require a preceding `file_tree`, `file_search`, attachment, or other path-discovery action merely to establish provenance. The tool itself remains responsible for checking that the path exists, has the expected type, is allowed for the authenticated role, and is accessible under the actual OS/filesystem permissions.

This rule is structural: it applies because the tool declares `work_kind = "inspection"`, not because its name appears in a hardcoded list.

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

## File path provenance

Path provenance is reserved for side-effecting file actions rather than read-only inspection.

Discovery and creation may establish concrete paths. Mutating or export-style file actions that operate on an existing path remain exposed only for established paths, and execution re-checks the same scope. Read-only inspection does not require that prior provenance step; attempting to inspect a nonexistent or inaccessible path fails visibly in the underlying tool.

Manual activation answers whether the model has explicitly requested a tool's full contract. Progress gating answers whether an inspection tool is still obtaining new structural information. Path provenance answers whether a side-effecting existing-file action is allowed to target a concrete path.
