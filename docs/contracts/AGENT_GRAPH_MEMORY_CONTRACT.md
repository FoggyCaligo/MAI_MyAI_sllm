# Agent Graph Memory Contract

## Status

This is the runtime memory contract for the current MAI redesign.

> The model decides meaning; the framework enforces structure.

There is one top-level Agent loop. There is no separate post-answer Memory-model loop and no scratchpad subsystem.

The memory system is divided structurally into:

- **Actual Graph**: durable graph state committed by completed prior turns;
- **Working Graph**: the turn-local graph region the current Agent has explicitly recalled, plus its pending generate/fix changes.

The Working Graph is discarded when the turn ends. Only its final mutation set is committed to the Actual Graph, and only after the final answer has been generated and frozen.

---

## 1. Turn lifecycle

```text
User request
  ↓
Agent round 1
  └─ mandatory memory/recall(query)
       ↓
       vector-similar node candidates
       ↓
Agent round 2+
  ├─ memory/recall(node_id)
  │    → selected Actual node + active one-hop
  │    → merge into Working Graph
  ├─ memory/generate/*
  │    → Working Graph only
  ├─ memory/fix/*
  │    → Working Graph only
  ├─ file/web/other work tools
  └─ more recall as needed
       ↓
Agent produces final answer with graph_synced=true
       ↓
answer text is frozen inside the lifecycle
       ↓
Working Graph mutation set
  → one atomic Actual Graph transaction
       ↓
commit succeeds
       ↓
frozen answer is returned to the UI
       ↓
Working Graph discarded
```

If the Agent fails, the Working Graph is discarded and the Actual Graph is not semantically mutated.
If final graph commit fails, the failure remains visible and the frozen answer is not returned to the UI.

Each explicit Agent round maps to exactly one structured model request. No hidden review/retry/fallback model request is inserted by the adapter.

---

## 2. Mandatory vector recall

The first Agent action of every turn is `memory/recall(query)`.
Before that succeeds, external tool routes and `answer` are not exposed.

The query is embedded using the `.env` configured model. Reference configuration:

```text
MAI_OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```

Vector recall returns several similar node candidates. Candidate retrieval alone does **not** open those nodes into the Working Graph.

The model chooses a candidate and calls `memory/recall(node_id)` to open it.
Embedding failure is a real failure. There is no lexical fallback.

---

## 3. Gradually expanding Working Graph

The Working Graph starts empty.

A node-id recall opens exactly:

- that Actual node;
- its currently active one-hop edges;
- the one-hop endpoint nodes.

Those objects are merged into the current Working Graph rather than replacing it.

```text
recall A
→ Working = A + A one-hop

recall B
→ Working = previous Working ∪ B + B one-hop
```

Therefore the world graph is never copied wholesale into the turn.
The Agent progressively brings only needed regions from the Actual Graph into its current workspace.

Working generate/fix changes overlay recalled Actual objects. A later recall never silently overwrites a pending Working change with the older Actual value.

New Working objects receive framework-owned negative temporary IDs. They may be referenced by later Agent rounds in the same turn. At final commit they are replaced by real SQLite IDs.

---

## 4. Actual Graph versus Working evidence

The Actual Graph is historical evidence from completed turns.
The Working Graph is the current Agent's pending interpretation and must never be represented as historical evidence.

For an opened edge, history inspection distinguishes explicitly between:

```text
actual_current
working_current
past_versions
```

This prevents a model from changing an edge in the current turn and then treating that change as proof of what it remembered before the turn.

A pending Working object is exposed with:

```text
pending: true
committed_at: null
```

Committed Actual objects are exposed with graph-record timestamps.

---

## 5. Graph timestamps

Nodes expose graph record timestamps such as:

```text
graph_created_at
graph_updated_at
```

Committed edge versions expose:

```text
committed_turn_id
committed_at
```

These timestamps mean **when MAI's graph record was created or changed**.
They do not mean the real-world time that the remembered fact was true.

For example, learning today that something happened in 2023 produces a graph update today. The framework must not infer the fact's real-world time from the graph update timestamp.
Any semantic temporal meaning such as "in 2023", "previously", or "currently" remains model-authored semantic content rather than a framework string heuristic.

Graph timestamps nevertheless give the model structural evidence that a state was only recently recorded and therefore should not automatically be treated as an old remembered state.

---

## 6. Node model

A node represents a semantic entity or concept.

Model-facing node data includes at least:

```text
node_id
name
kind
source_ids[]
member_node_ids[]
pending
graph_created_at
graph_updated_at
```

Kinds:

- `concept`
- `composite`

Composite membership is structural graph data, not a special relation string. Self-membership and membership cycles are invalid.

### Reuse first

Before each new node generation, the Agent must perform a fresh relevant vector query recall.
The framework enforces the lookup step but does not decide semantic equivalence with string matching, aliases, or hard-coded dictionaries.

The model decides whether an existing candidate represents the intended concept.
Existing nodes are reused/fixed before new ones are generated.

Maximum new nodes per turn: **10**.
There is no permanent node-degree cap.

### Pending node IDs

New nodes exist only in the Working Graph until final commit and use negative temporary IDs.
They may participate in Working edges/composites during the same turn.

---

## 7. Directed edge model

A logical edge is identified by its ordered endpoints:

```text
(user_id, start_node_id, end_node_id)
```

A → B and B → A are separate logical edges.
Parallel A → B logical edges are not allowed merely because relation wording differs.

The logical edge stores stable endpoints. Its semantic state is stored in committed versions.

```text
Logical Edge
- edge_id
- start_node_id
- end_node_id

Committed Edge Version
- version_id
- relation
- weight
- personal_relevance
- committed_turn_id
- committed_at
- source_ids[]
```

---

## 8. Edge versions

The Actual Graph retains at most the **three most recent committed turn states** for one logical edge.

The history is turn-based, not round-based.
Repeated fixes to the same Working edge within one Agent turn do not create multiple committed versions.
Only the final Working state becomes one new version when the turn commits.

Example:

```text
turn 10 → developer
turn 25 → web developer
turn 48 → backend developer  (current)
```

During turn 49 the model may change the Working relation many times, but none of those intermediate states are Actual history.
At successful final commit only the final turn-49 state is appended.
After pruning, only the newest three committed turn states remain model-facing history.

Version-specific source links are preserved so evidence for one state is not silently reassigned to another state.

---

## 9. Edge generation and fixing

### Generate

`memory/generate/edge` creates a pending directed edge only when the ordered pair has no logical edge.

If an Actual or Working logical edge already exists for the pair, generation is rejected visibly and its edge ID is returned so the model can use `memory/fix/edge`.
There is no silent generate-to-fix fallback.

### Fix

`memory/fix/edge` changes the Working state only.
It may:

- change relation;
- apply `weight_delta`;
- promote personal relevance;
- attach current evidence;
- disconnect the relationship.

`weight_delta` is applied to the Working edge's current value and structurally clamped to 0.0–1.0.

### Disconnect

There is no semantic hard delete operation.
Disconnect is represented by the final committed edge version having:

```text
weight = 0
```

A zero-weight logical edge remains available for provenance/history and future repair, but ordinary active one-hop recall excludes it.

### Mutation budget

Each node may participate in at most 10 edge mutations during one turn.
The budget is per turn, not a permanent degree limit.

---

## 10. Personal relevance

`weight` and `personal_relevance` are separate.

Framework mapping:

```text
user_centered      → 1.0
general_knowledge  → 0.5
```

The model chooses the classification. The framework does not infer it from text patterns.
A lower-relevance observation does not automatically demote an already user-centered edge.

---

## 11. Sources and provenance

Evidence units are stored durably and referenced by numeric `source_id`.

Nodes link to sources.
Edge evidence links to a **specific committed edge version**, not merely to the logical edge.

This allows MAI to distinguish:

```text
version A was supported by source 12
version B was supported by source 81
```

Source records may be created while the turn is running, but pending semantic graph state is not committed until final graph commit. An aborted turn may leave unlinked evidence records; it does not leave durable semantic graph mutations.

---

## 12. Memory tools

```text
memory/
├─ recall
├─ generate/
│  ├─ node
│  └─ edge
└─ fix/
   ├─ node
   └─ edge
```

### `memory/recall`

Modes:

- `query`: vector-similar Actual node candidates;
- `node_id`: open Actual node + active one-hop into Working Graph;
- `edge_id`: inspect Working state separately from committed Actual current/history.

Repeated node recalls expand the same Working Graph.

### `memory/generate/node`

Creates a pending semantic/composite node after a fresh query recall.

### `memory/generate/edge`

Creates a pending directed edge for a previously unused ordered pair.

### `memory/fix/node`

Stages node rename, composite-member repair, or duplicate merge inside the Working Graph.

### `memory/fix/edge`

Stages relation/weight/relevance/source changes or disconnect.

---

## 13. Final graph-sync gate and answer freeze

The same Agent loop decides when its Working Graph matches its latest understanding.
The final action must contain:

```text
graph_synced = true
```

There is no additional model review call.

`graph_synced=true` means:

> The current Working Graph is the Agent's intended final durable memory state for this turn.

The resulting answer text is frozen before graph persistence begins.
The framework then commits the Working Graph mutation set in one transaction.
Only after that succeeds is the frozen answer returned to the UI.

The framework does not semantically compare answer text against graph contents.

---

## 14. External tool discovery

Memory is a core Agent capability.
Large external tool schemas are exposed lazily through exact structural routes.

Examples:

```text
/file/tree/manual
/file/tree/use
/file/read/use
/web/search/use
/web/market/use
/web/current/use
```

Invalid paths fail visibly. No fuzzy route correction or substring routing is used.
External namespaces become available after the mandatory first vector recall.

---

## 15. Failure rules

Failures remain visible.

- no lexical fallback for failed embedding recall;
- no text heuristics for semantic identity/routing;
- no guessed tool route;
- no silent generate-to-fix conversion;
- no hidden model re-request;
- no permanent semantic DB write during Agent graph work;
- commit conflicts roll back the final graph transaction;
- a failed final commit prevents the frozen answer from reaching the UI;
- self-reference, ownership, cycle, duplicate-pair, and budget violations remain explicit errors.

The model owns meaning. The framework owns structural validity and transaction boundaries.

---

## 16. Retired architecture

The following are retired:

- dedicated Qwen memory-model orchestration;
- post-answer `GraphCommitPhase` model loop;
- `continue_memory` protocol;
- scratchpad memory tools/registry;
- direct semantic mutation of the Actual Graph on every Agent round;
- using current-turn edits as historical memory evidence.

The current design is one Agent loop operating on a gradually expanded Working Graph, followed by an answer freeze and one atomic promotion of final graph changes into the Actual Graph.
