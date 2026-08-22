# Mai

**English** | [한국어](README.ko.md)

> README maintenance rule: `README.ko.md` is the canonical source. Keep this English README synchronized to the same structure and level of detail.

Mai is a **personal local semi-GPT project that extends a local sLLM with persistent graph memory and practical PC/web tools**.

The core design principle is:

> **The model decides meaning; the framework enforces structure.**

The current branch rebuilds memory around **Actual Graph + turn-local Working Graph + periodic graph checkpoints**.

Canonical memory contract:

- [`docs/contracts/AGENT_GRAPH_MEMORY_CONTRACT.md`](docs/contracts/AGENT_GRAPH_MEMORY_CONTRACT.md)

---

# 1. Target architecture

```text
User
 ↓
Recent conversation context
 ↓
Mai Agent lifecycle
 ├─ mandatory vector memory recall
 ├─ turn-local Working Graph
 ├─ memory recall / generate / fix
 ├─ file / document / image / code / terminal
 ├─ web / market / current info
 ├─ periodic graph checkpoint
 └─ final graph checkpoint → answer
```

There is no separate post-answer Memory LLM and no scratchpad subsystem.
The same configured main model is used for ordinary Agent rounds and graph-only checkpoints.

The graph is split structurally into:

1. **Actual Graph**: durable long-term memory committed by completed prior turns;
2. **Working Graph**: Actual Graph regions explicitly recalled during the current turn plus current-turn pending mutations.

The Working Graph is the current turn's cognition/workspace and is not treated as past-memory evidence.

---

# 2. Memory flow for one turn

The first Main round cannot answer immediately.
It must perform at least one `memory/recall(query)` action first.

```text
User message
 ↓
Main round 1
 ↓
memory/recall(query)
 ↓
Embedding vector similarity search
 ↓
Several related node candidates
```

Query recall returns candidates only. It does not automatically open them into the Working Graph.
When the model chooses a candidate and calls `memory/recall(node_id)`, that Actual node and its active one-hop are accumulated into the Working Graph.

```text
recall A
→ Working = A + A active one-hop

recall B
→ Working = previous Working + B + B active one-hop
```

`memory/generate/*` and `memory/fix/*` during Main work mutate only the Working Graph.
New Working nodes use framework-issued negative temporary IDs and can be referenced by later rounds in the same turn.

After a configured number of Main LLM requests, the framework forces a graph-only checkpoint.
The current default interval is **3 Main LLM requests**.

```text
Main #1
Main #2
Main #3
 ↓
Graph Checkpoint
 ↓
Main #4 ...
```

Every answer candidate triggers a mandatory Final Graph Checkpoint regardless of the current count.
If the periodic boundary and the final checkpoint coincide, only one checkpoint runs.

After the Final Graph Checkpoint completes, the Working Graph mutation set is committed to the Actual Graph in **one atomic transaction with no LLM call**, and only then is the answer returned.

---

# 3. Vector recall

`memory/recall(query)` retrieves candidate nodes by embedding-vector similarity rather than lexical substring matching.

The embedding model is configured separately in `.env`.

Reference configuration:

```env
MAI_OLLAMA_MODEL=gemma4:e4b
MAI_OLLAMA_EMBEDDING_MODEL=nomic-embed-text
MAI_OLLAMA_IMAGE_MODEL=gemma4:12b
```

The embedding model is not a second reasoning/memory LLM. It only generates query/node vectors.

Embedding failure is surfaced. Mai does not silently fall back to lexical recall.

---

# 4. Memory tool API

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

## `memory/recall`

- `query`: retrieve vector-similar node candidates;
- `node_id`: open one Actual node and its active one-hop into the Working Graph;
- `edge_id`: inspect Actual/Working/history state for one edge.

## `memory/generate/node`

Creates a semantic concept node or composite node in the Working Graph.
A fresh relevant query recall must occur before each new node generation.
If the Agent judges an existing candidate semantically equivalent, it must reuse that node rather than create a duplicate.

Maximum new nodes per turn: **10**.
Composite nodes consume the same budget.

## `memory/generate/edge`

Edge direction is represented by start/end endpoints.

```text
A -> B
B -> A
```

are distinct logical edges.
Only one logical edge exists for the same `(start_node_id, end_node_id)` ordered pair.
Different relation wording does not justify another parallel edge.

## `memory/fix/node`

- update node state;
- update composite membership;
- merge duplicate nodes.

## `memory/fix/edge`

- update the current relation;
- apply `weight_delta`;
- update personal relevance;
- add source evidence;
- disconnect.

Disconnect sets weight to `0` rather than deleting the edge.
Zero-weight edges remain available for provenance/debug/history but are excluded from normal active recall.

---

# 5. Node / Edge structure

## Node

```text
node_id
name
kind: concept | composite
source_ids[]
member_node_ids[]
pending
graph_created_at
graph_updated_at
```

Composite membership is framework-owned structural data rather than a normal model-authored relation string.
Self-membership and membership cycles are invalid.

## Edge

```text
edge_id
start_node_id
end_node_id
relation
weight
personal_relevance
current_version_id
```

There is no permanent degree cap on a node.
Turn execution budgets are:

- new nodes: at most 10 per turn;
- semantic edge mutations: at most 10 per participating node per turn.

One edge mutation consumes budget for both its start and end nodes.

---

# 6. Weight and personal relevance

They are separate concepts.

## Weight

Represents the current strength of a directed relationship.

- range: `0.0 ~ 1.0`;
- updates use `+/- delta` against the existing value;
- `0.0` means disconnected.

## Personal relevance

Represents how directly the memory concerns the user.

```text
user_centered      = 1.0
general_knowledge  = 0.5
```

The Agent chooses the classification.
The framework does not infer it from keywords.

Source reliability/confidence remains a separate axis.

---

# 7. Sources / provenance / history

Nodes and edges link to source evidence relationally.
Edge evidence links to a specific committed edge version.

Current-turn Working state is explicitly separated from prior Actual history.

```text
actual_current
working_current
past_versions
working_state_is_past_evidence: false
```

Repeated fixes to the same edge in one turn produce one final committed state for that turn.
Each logical edge retains the most recent **3 committed turn states**.

This prevents a Working state created moments ago from being reused as proof of what the system remembered before the current turn.

---

# 8. Working Graph mutation and final commit

Semantic memory mutations apply immediately to the **Working Graph** during Main/checkpoint execution, but they are not immediately committed to the Actual Graph.

```text
Main / Checkpoint
→ memory/generate or memory/fix
→ Working Graph changes
→ later rounds can use the changed Working Graph immediately
```

The durability boundary is the atomic commit after the Final Graph Checkpoint.

```text
Final Graph Checkpoint complete
 ↓
Working Graph mutation set
 ↓
SQLite atomic transaction
 ↓
Actual Graph
 ↓
frozen answer returned
```

If the Agent/checkpoint fails, or if final commit fails, Working semantic changes are not promoted to the Actual Graph.
Commit failure remains visible and the frozen answer is not returned.

---

# 9. Periodic / Final Graph Checkpoints

A graph checkpoint is not a separate Memory reviewer model. It reuses the **same main model in a graph-only cognition state**.

During a checkpoint, answer and external work tools are unavailable.
Only memory actions or explicit `sync_complete` are legal.

Each checkpoint LLM request still produces **exactly one action**.

```text
Checkpoint LLM
→ one memory action
→ apply the real result to Working Graph
→ if sync_complete=false, run another checkpoint LLM request
→ Main cannot resume until the checkpoint completes
```

If no memory action is needed, the model returns `sync_complete`.
If one final memory action is sufficient, that action can carry `sync_complete=true`, avoiding a separate done call.

The framework does not decide what is worth remembering through text matching or topic heuristics.
It only enforces checkpoint timing, legal actions, and the commit boundary.

---

# 10. Lazy tool hierarchy

Mai does not expose every work-tool schema to a small sLLM at once.

Initial external namespaces stay small:

```text
/file
/web
```

Examples:

```text
/file/tree
/file/tree/manual
/file/tree/use

/web/search
/web/market
/web/current
```

If the model already understands a tool during the same Agent loop, it may request the exact `/.../use` path directly.

An invalid route returns a structured error and valid children. The framework does not guess a similar path.

Memory is a core Agent capability and is not hidden behind the external-tool namespace.

---

# 11. Existing PC / Web capabilities

The non-memory runtime should be preserved as much as possible.

Owner capabilities include:

- file tree/search/read/CRUD/download;
- document read;
- image analysis;
- code index/search;
- terminal commands;
- web search;
- market data;
- current/latest information.

Trial accounts keep per-user graph memory and only the permitted web/attachment capabilities.

Tool/OS/file failures remain visible.

---

# 12. Removed old memory runtime

- dedicated `MAI_OLLAMA_MEMORY_MODEL`;
- post-answer `GraphCommitPhase`;
- `continue_memory`;
- separate post-answer Memory-model loop;
- `ScratchpadRegistry`;
- `scratchpad_put`;
- `scratchpad_update`;
- scratchpad → durable-memory promotion;
- old `node_lookup` + `recall_memory` split API.

---

# 13. Installation overview

```powershell
git clone https://github.com/FoggyCaligo/MAI_MyAI_sllm.git
cd MAI_MyAI_sllm
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python run_server.py
```

Pull the actual reference Ollama models listed in `.env.example`.

---

# 14. Data

```text
data/graph.sqlite3
```

Contains semantic nodes/edges, edge versions, provenance/source links, and the long-term Actual Graph.

```text
data/chat.sqlite3
```

Contains raw conversation history, sessions, persistent chat jobs, and compact recent tool-operation history.

For a complete backup, stop Mai normally and back up the whole `data/` directory.

---

# 15. Failure behavior

Mai does not hide failures through fallback behavior.

- no semantic string routing;
- no lexical fallback when vector recall fails;
- no guessed tool path;
- no hidden model retry;
- no silent generate→fix conversion;
- no silent duplicate graph creation;
- no silent Working→Actual commit failure.

Structural contract violations fail visibly.
