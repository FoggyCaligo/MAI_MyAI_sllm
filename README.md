# Mai

**English** | [한국어](README.ko.md)

> README maintenance rule: `README.ko.md` is the canonical source. Keep this English README synchronized to the same structure and level of detail.

Mai is a **personal local semi-GPT project that extends a local sLLM with persistent graph memory and practical PC/web tools**.

The core design principle is:

> **The model decides meaning; the framework enforces structure.**

This branch first freezes the new memory architecture in documentation, then rebuilds the implementation by removing the existing memory subsystem and layering the new memory design onto the non-memory runtime.

Canonical memory contract:

- [`docs/contracts/AGENT_GRAPH_MEMORY_CONTRACT.md`](docs/contracts/AGENT_GRAPH_MEMORY_CONTRACT.md)

---

# 1. Target architecture

```text
User
 ↓
Recent conversation context
 ↓
Mai single Agent loop
 ├─ mandatory vector memory recall
 ├─ turn-scoped ViewedGraph
 ├─ persistent graph generate/fix
 ├─ file / document / image / code / terminal
 ├─ web / market / current info
 └─ answer
```

There is no separate post-answer Memory LLM in the target architecture.
The request-scoped scratchpad is also removed.

The graph serves two roles:

1. long-term memory across turns and restarts;
2. the memory substrate the Agent directly inspects and updates while working.

---

# 2. Memory flow for one turn

The Agent cannot answer immediately on its first round.
It must perform at least one `memory/recall(query)` action first.

```text
User message
 ↓
Agent round 1
 ↓
memory/recall(query)
 ↓
Embedding vector similarity search
 ↓
Several related node candidates
 ↓
Agent chooses useful node_id
 ↓
memory/recall(node_id)
 ↓
Selected node + active one-hop
 ↓
Merged into ViewedGraph
```

Further recalls do not replace previous results.

```text
ViewedGraph(next)
= ViewedGraph(current)
+ newly opened nodes/edges
```

The ViewedGraph is discarded when the turn ends.
Persistent graph mutations remain in SQLite and are recallable in future turns.

---

# 3. Vector recall

`memory/recall(query)` targets embedding-vector similarity rather than lexical substring matching.

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

Two modes:

- `query`: retrieve vector-similar node candidates;
- `node_id`: open one selected node and its active one-hop neighborhood.

## `memory/generate/node`

Creates a semantic concept node or composite node.
A relevant query recall must have occurred first.
If the Agent judges an existing candidate semantically equivalent, it must reuse that node rather than create a duplicate.

Maximum new nodes per turn: **10**.
Composite nodes consume the same budget.

## `memory/generate/edge`

Edge direction is represented by start/end endpoints.

```text
A -> B
B -> A
```

are distinct edges.

Only one current semantic edge exists for the same `(start_node_id, end_node_id)` ordered pair.
A different relation wording does not justify another parallel edge.

## `memory/fix/node`

- update a node;
- update composite membership;
- merge duplicate nodes.

## `memory/fix/edge`

- update the current relation;
- apply `weight_delta`;
- update personal relevance;
- add source evidence;
- disconnect.

Disconnect sets weight to `0` rather than deleting the edge.
Zero-weight edges remain available for provenance/debugging but are excluded from normal active recall.

---

# 5. Node / Edge structure

## Node

```text
node_id
name
kind: concept | composite
source_ids[]
```

A composite node represents a new concept constituted by multiple existing nodes.
Membership is framework-owned structural data rather than a normal model-authored relation string.
Self-membership and composite cycles are invalid.

## Edge

```text
edge_id
start_node_id
end_node_id
relation
weight
personal_relevance
source_ids[]
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

# 7. Sources and provenance

Nodes and edges may independently reference source sentence/tool evidence IDs.

Model-facing form:

```text
source_ids: [12, 18, 44]
```

The database stores these links relationally rather than as one opaque JSON list.

The abandoned design of stacking up to three historical edge versions is not used.
The current graph represents the current understanding; provenance preserves why that state exists.

---

# 8. Graph mutations commit immediately

Memory is not deferred until after the final answer.

```text
Agent round N
→ memory/generate or memory/fix
→ immediate SQLite commit

Agent round N+1
→ updated graph can be recalled immediately
```

The same graph remains recallable in future turns.

This means an incorrect intermediate judgment can be persisted.
The target behavior is to repair the current graph through `memory/fix/*` as the Agent's understanding changes rather than hide intermediate state behind a deferred post-answer transaction.

---

# 9. Final graph-sync gate

Mai does not add another Memory reviewer model.

Before the same Agent loop terminates with an answer, it must explicitly confirm:

> Does the persistent graph / ViewedGraph match the latest durable understanding gained in this turn?

If not, it performs another memory generate/fix round.
Only an answer that confirms graph alignment may terminate the loop.

The framework does not compare answer text and graph meaning with string heuristics.

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

# 12. Old memory runtime to remove

During implementation, the following old memory responsibilities are removed:

- dedicated `MAI_OLLAMA_MEMORY_MODEL`;
- post-answer `GraphCommitPhase`;
- `continue_memory`;
- memory loop after the final answer;
- `ScratchpadRegistry`;
- `scratchpad_put`;
- `scratchpad_update`;
- scratchpad → durable-memory promotion;
- the old split `node_lookup` + `recall_memory` API.

The non-memory runtime is preserved first, then the new memory subsystem is layered back onto it.

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

Contains persistent semantic graph memory and provenance/source links.

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
- no silent duplicate graph creation.

Structural contract violations fail visibly.
