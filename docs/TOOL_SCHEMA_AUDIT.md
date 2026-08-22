# Work Tool Schema Audit

This audit records the runtime schema contract used by Mai's deferred work-tool manuals and the coverage that protects it.

## Common work-tool envelope

Every work tool exposed through `AgentLifecycle` must provide a top-level JSON Schema object with:

- `action = "tool"`
- `tool = <actual tool name>`
- `arguments = <tool-specific input schema>`

The tool-specific `arguments` schema may itself contain `oneOf` or other JSON Schema composition. The common envelope must remain stable so `tool_manual` can expose the argument contract without understanding individual tool semantics.

## Audited runtime builders

The audit covers the actual owner runtime builders:

- file inspection tools
- file mutation tools
- document/image tools
- terminal tool
- code tools
- web/market tools
- scratchpad tools

It also checks dynamic `schema_for_paths()` variants where applicable.

## Grounding capability contract

Tool routing is still model-driven. Mai does not inspect the user's text to decide which tool should run.

For answer grounding, runtime tool capability is derived structurally from each tool's existing `evidence_kind` metadata. Tools wrapped with `evidence_kind="web_evidence"` are configured as grounding-capable tools on the chat model. When an answer review requests more external evidence:

- only already exposed grounding-capable tools remain executable;
- `tool_manual` is restricted to grounding-capable targets;
- unrelated file, terminal, mutation, memory, or scratchpad tools are not exposed for that grounding retry;
- tracked results from explicitly typed evidence tools preserve `evidence_kind` and `evidence_id`, so structured market results such as `market_snapshot` can participate in grounding just like web search results.

This prevents a grounding retry from drifting into an unrelated capability while avoiding query-string routing or tool-name heuristics.

## Regression coverage

The test suite checks:

1. every runtime work tool preserves the common schema envelope;
2. dynamic schemas preserve the same envelope;
3. each work tool can pass through the actual deferred `tool_manual -> next agent action` path;
4. market lookup/snapshot variants remain under `properties.arguments.oneOf`;
5. explicit evidence kinds survive evidence tracking without changing the generic tool-result contract;
6. `market_snapshot` evidence is visible to answer grounding;
7. grounding retries structurally exclude non-grounding tools and filter `tool_manual` targets accordingly;
8. `WorkingMemoryLifecycle` derives the configured grounding tool set from `evidence_kind`, rather than from user-text heuristics.
