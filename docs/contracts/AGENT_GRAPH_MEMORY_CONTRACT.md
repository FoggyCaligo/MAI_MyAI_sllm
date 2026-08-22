# Agent Graph Memory Contract

## Status

This is the canonical runtime memory contract for the current MAI redesign.

> The model decides meaning; the framework enforces structure.

There is one Agent lifecycle using one configured main model. Graph memory is part of that lifecycle; there is no separate post-answer Memory model and no scratchpad subsystem.

The graph is divided structurally into:

- **Actual Graph**: durable graph state committed by completed prior turns.
- **Working Graph**: the turn-local graph region explicitly recalled during the current turn, plus pending generate/fix changes.

The Working Graph is never copied wholesale from Actual Graph. It grows only through explicit recall and current-turn pending mutations.

---

## 1. Turn lifecycle and periodic graph checkpoints

Each normal Main round is exactly one structured LLM request producing exactly one action.

The model may use memory tools during Main work whenever those schemas are available. Independently of that voluntary use, the framework enforces a periodic graph checkpoint after a configured number of Main LLM requests. The current default is **3 Main rounds**.

```text
User request
  ↓
Main #1
  ↓
Main #2
  ↓
Main #3
  ↓
Mandatory Graph Checkpoint
  ├─ one LLM request → one memory action
  ├─ apply result to Working Graph
  ├─ repeat inside checkpoint only when sync_complete=false
  └─ checkpoint exits only when complete
  ↓
Main #4
  ↓
...
```

The checkpoint interval counts **Main LLM requests**, not memory-tool calls and not checkpoint LLM requests.

A graph checkpoint is a distinct graph-only phase. It may use only:

- `memory/recall`
- `memory/generate/node`
- `memory/generate/edge`
- `memory/fix/node`
- `memory/fix/edge`
- explicit `sync_complete` when no memory action is required

External file/web/work tools and user-facing answers are unavailable inside a checkpoint.

A checkpoint memory action carries `sync_complete: false|true`.

- `false`: after the action result is applied, another checkpoint LLM request is required.
- `true`: successful application of that action completes the checkpoint.
- explicit `sync_complete`: completes the checkpoint without a memory action when no graph change or recall is needed.

The framework does not infer graph consistency from strings or topic heuristics. The model decides semantic content; the checkpoint state structurally forces the model to inspect graph memory before work may continue.

---

## 2. Final answer checkpoint

A Main answer is an **answer candidate**, not yet returned to the UI.

Regardless of how many Main rounds have occurred since the previous periodic checkpoint, every answer candidate is followed by a mandatory **Final Graph Checkpoint**.

```text
Main → answer candidate
  ↓
Final Graph Checkpoint
  ├─ graph-only LLM request(s)
  ├─ Working Graph recall/generate/fix as needed
  └─ complete
  ↓
answer candidate remains fixed
  ↓
Working Graph mutation set
  → one atomic Actual Graph transaction
  ↓
commit succeeds
  ↓
frozen answer returned to UI
```

If the answer arrives exactly when a periodic checkpoint would otherwise be due, the final checkpoint replaces that periodic checkpoint. Two checkpoints are not run back-to-back for the same boundary.

The old `graph_synced=true` self-report on the Main answer is not the final safety mechanism. Completion of the mandatory final checkpoint is the structural gate.

The Actual Graph commit performs no LLM call.

If the Agent/checkpoint fails, pending Working Graph changes are discarded. If final commit fails, the failure remains visible and the answer candidate is not returned.

---

## 3. Mandatory initial vector recall

The first Main action of every turn is `memory/recall(query)`.

Before that succeeds:

- `answer` is unavailable;
- external work-tool routes are unavailable.

The query is embedded using the configured embedding model, e.g.:

```text
MAI_OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```

Query recall returns vector-similar node candidates only. Candidate retrieval does **not** open those nodes into the Working Graph.

Embedding failure is a real failure. There is no lexical fallback.

---

## 4. Working Graph expansion

The Working Graph starts empty.

`memory/recall(node_id)` opens:

- the selected Actual node;
- its currently active one-hop edges;
- the one-hop endpoint nodes.

Opened regions accumulate:

```text
recall A
→ Working = A + active A one-hop

recall B
→ Working = previous Working ∪ B + active B one-hop
```

Pending Working mutations overlay recalled Actual objects. Later recall does not silently overwrite current-turn pending changes with older Actual values.

New Working nodes and edges use framework-owned negative temporary IDs. They remain pending until the final atomic commit.

Working Graph therefore means:

> the part of graph memory the current Agent has actually opened this turn, plus the current turn's not-yet-committed interpretation.

---

## 5. Actual versus Working evidence

Actual Graph state from completed turns can serve as past memory evidence.

Working Graph state is the current turn's interpretation and must **not** be presented as historical evidence merely because it exists in Working Graph.

Edge history distinguishes:

```text
actual_current
working_current
past_versions
working_state_is_past_evidence: false
```

This prevents the Agent from writing a claim during the current turn and then citing its own write as proof of an older memory.

---

## 6. Node contract

Node kinds:

- `concept`
- `composite`

Composite membership is structural data in `graph_composite_members`. Composites may contain composites, but self-membership and cycles are invalid.

Before generating a new node, a fresh relevant query recall is required. The framework enforces the lookup requirement but never decides semantic equivalence through string matching, aliases, or hard-coded text heuristics.

The model chooses whether to reuse/fix an existing candidate or generate a new node.

Maximum new nodes per turn: **10**.

Pending nodes use negative temporary IDs and may participate in other Working Graph structures during the same turn.

---

## 7. Directed edge contract

A logical directed edge is identified by:

```text
(user_id, start_node_id, end_node_id)
```

A → B and B → A are distinct. Parallel logical edges for the same ordered pair are not created merely because relation wording differs.

`weight=0` means inactive/disconnected; the logical edge row remains. Ordinary one-hop recall excludes inactive edges.

Each logical edge retains the latest **3 committed turn states**. Repeated Working edits in one turn produce only the final committed state for that turn.

Each participating node may receive at most **10 edge mutations per turn**.

---

## 8. Provenance

Evidence is stored through:

- `graph_sources`
- `graph_source_links`

Source kinds include:

- `user_message`
- `assistant_message`
- `web_evidence`
- `file_evidence`
- `tool_operation`

Nodes link to source records. Edge evidence links to a specific committed edge version.

Source records may exist without semantic graph links if a turn later fails; failed turns must not leave committed semantic graph mutations.

---

## 9. Timestamps

Graph timestamps describe **graph-record time**, not real-world fact-validity time.

Nodes expose record metadata such as:

```text
graph_created_at
graph_updated_at
```

Committed edge versions expose:

```text
committed_turn_id
committed_at
```

Learning today that an event happened years ago updates the graph today. The framework must not infer event time from record timestamps or from string heuristics. Semantic temporal meaning remains model-authored graph content.

---

## 10. Atomic final commit

During Main work and graph checkpoints, Actual Graph semantic state is read-only. Generate/fix operations mutate only Working Graph.

After the final checkpoint completes, `commit_working_graph(...)` applies the pending semantic mutation set in one SQLite transaction.

The commit:

- materializes pending temporary nodes;
- applies node updates and validated merges;
- materializes pending edges;
- writes one committed edge version per touched logical edge for the turn;
- links provenance;
- prunes retained edge history according to the committed-turn policy.

There is no post-commit LLM review and no fallback semantic write path.

---

## 11. Failure principles

Contract violations fail visibly.

The runtime must not hide failures through:

- lexical or string-based semantic fallbacks;
- fuzzy route correction;
- silent duplicate-node creation when required reuse checks were not performed;
- helper logic that bypasses the model-facing graph contract;
- silent Actual Graph mutation during Main/checkpoint phases.

The model decides meaning. The framework enforces scopes, budgets, cadence, graph visibility, transactionality, and allowed state transitions.
