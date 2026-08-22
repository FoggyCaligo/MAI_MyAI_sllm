# Mai Working-Memory Contract

## Status

The request-scoped scratchpad design is retired.
The persistent semantic graph is now also the Agent's working-memory substrate, while each turn maintains only a temporary in-memory `ViewedGraph` describing which persistent nodes/edges the Agent has actually opened.

The canonical graph behavior is defined in [`AGENT_GRAPH_MEMORY_CONTRACT.md`](AGENT_GRAPH_MEMORY_CONTRACT.md).

---

## 1. No scratchpad layer

The target runtime does not use:

- `ScratchpadRegistry`;
- `scratchpad_put`;
- `scratchpad_update`;
- scratchpad IDs;
- a post-answer promotion step from scratchpad to graph memory.

Temporary task state that matters to reasoning is represented by normal Agent tool history plus the turn-scoped ViewedGraph.
Durable semantic information is written directly to the persistent graph through `memory/generate/*` or `memory/fix/*` and is committed immediately.

---

## 2. ViewedGraph lifetime

Each user turn starts with a new empty ViewedGraph.

`memory/recall(query=...)` performs vector candidate retrieval but does not automatically expand candidate neighborhoods into the ViewedGraph.

When the Agent opens a node:

```text
memory/recall(node_id=N)
```

Framework loads the selected node plus its active one-hop neighborhood and merges those nodes/edges into the current ViewedGraph.

Further recalls accumulate:

```text
ViewedGraph(next) = ViewedGraph(current) ∪ newly opened one-hop graph
```

Previous recall results remain visible during the same turn.
The ViewedGraph is discarded at turn completion/failure. It is not persistent state by itself.

Persistent graph writes performed during the turn are not discarded.

---

## 3. Graph mutations refresh working state

Graph mutations commit immediately to SQLite.

After `memory/generate/*` or `memory/fix/*`, affected nodes/edges in the current ViewedGraph must be refreshed from the committed graph state so later rounds do not reason over stale copies.

The ViewedGraph therefore follows the Agent's evolving persistent graph understanding during the turn.

---

## 4. Attachment and tool evidence

Attachments and ordinary work-tool results may become provenance sources for graph nodes/edges, but there is no intermediate scratchpad requirement.

Attachment routing remains structural:

- known text/code suffix → strict configured text decoding;
- `.pdf` → PDF reader;
- `.docx` → DOCX reader;
- known image suffix → configured vision model;
- unsupported type → explicit unsupported state.

Reader/model/decode failures are surfaced rather than silently routed to another reader.

Tool evidence source kind is structural metadata declared by the tool/route registration, not inferred from tool-name text.

When the Agent uses a source to generate/fix memory, model-facing nodes/edges may expose:

```text
source_ids: [12, 18, 44]
```

The database stores source links relationally, not as opaque JSON arrays.

---

## 5. Mandatory first recall

Every turn must begin its Agent reasoning with at least one semantic `memory/recall(query=...)` action before a final answer action is available.

This requirement exists to prevent the small local model from relying only on the recent raw-chat window and incorrectly claiming that older user context is unavailable.

The framework enforces protocol state only. The Agent chooses the semantic query.

The mandatory query recall returns vector-similar candidates. The Agent chooses which candidate node(s) to open into the ViewedGraph.

---

## 6. Final graph-sync gate

Before the Agent may terminate with an answer, it must explicitly confirm that the persistent graph state it has worked with is aligned with its latest understanding of durable information from the turn.

If not aligned, it performs another normal memory generate/fix round.

This is not a separate review model and not a hidden additional LLM phase. It is a termination condition of the same Agent loop.

The framework does not compare answer text and graph contents semantically.

---

## 7. Failure visibility

The working-memory layer does not introduce fallback behavior.

The following remain explicit failures:

- embedding model failure;
- unknown/inactive node ID;
- graph scope violation;
- invalid source ID;
- invalid composite membership;
- node/edge mutation budget exhaustion;
- malformed memory action;
- file/document/image parsing or tool execution errors.

No lexical recall fallback is used when vector recall fails.
No string heuristic decides whether a memory should be generated, fixed, merged, or recalled.
