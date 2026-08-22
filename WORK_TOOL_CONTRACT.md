# Work Tool Contract

Mai work tools are structurally divided by execution behavior rather than by natural-language or tool-name heuristics.

## User-facing answer channel

User-facing conversational output is delivered only through the structured `answer` action.

A file payload, terminal stdout, search result, document extraction, or any other tool result is evidence/work state returned to the agent loop. It is never implicitly promoted into the final chat response. The model must terminate the turn with an `answer` action, whose `content` is fixed by the Framework before memory mutation and then released unchanged after memory mutation succeeds.

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

They declare both:

```text
work_kind = "action"
action_scope = "..."
```

An action tool without a non-empty `action_scope` is a registration/contract error. The Framework does not infer an action scope from a tool name or user text.

Current action scopes are declared by the tools themselves:

- file actions: `action_scope = "file"`
  - `file_create`
  - `file_update`
  - `file_delete`
  - `file_download_link`
- terminal actions: `action_scope = "terminal"`
  - `terminal_command`
- generic `FunctionWorkTool`: `action_scope = "generic"` by default

### Side-effect activation gate

At the beginning of an agent turn, action tools are not included in the model schema. The Framework exposes only a structural control action for the scopes actually declared by registered action tools:

```json
{
  "action": "request_action_scope",
  "scope": "file"
}
```

The model decides whether a side effect is actually part of the task. The Framework does not inspect the user's text to make that semantic decision.

After a valid scope request:

- that scope becomes active for the current turn only;
- action tools declaring that scope may appear in later model schemas;
- existing path-provenance restrictions still apply independently;
- the same already-open scope is no longer requestable;
- unrelated action scopes remain closed.

For example, a normal conversational turn can terminate directly through `answer` without ever exposing `file_create` or `terminal_command`. A file-creation task must first request the `file` action scope, then execute the file action, and finally return the user-facing result through `answer`.

Action failures remain visible. The Framework does not silently retry, redirect, rewrite an action into another tool, or treat a tool result as the final conversational response.

## File path provenance

The existing current-turn path provenance contract remains independent from progress gating and action-scope activation.

Discovery and creation may establish concrete paths. Existing-file actions are only exposed for established paths after their action scope is active, and execution re-checks the same path scope. Progress gating answers whether an inspection tool is still obtaining new structural information; action-scope activation answers whether state-changing tools are eligible to be shown at all.
