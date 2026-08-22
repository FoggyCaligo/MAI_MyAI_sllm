# Agent Graph Memory Contract

## Status

This document records the agreed target architecture for the next MAI runtime revision.
It replaces the dedicated post-answer memory-model design as the intended runtime direction.

The design principle is:

> The model decides meaning; the framework enforces structure.

The persistent semantic graph is both long-term memory and the agent's working memory during a turn.
The agent may recall, create, and fix graph state between normal tool rounds, and each accepted graph mutation is committed to SQLite immediately so later rounds can observe it.

---

## 1. Runtime shape

There is one top-level Agent loop.
There is no independent post-answer Memory loop.
There is no dedicated memory model requirement.
There is no request-scoped scratchpad layer.

```text
User request
  ↓
Agent round 1
  └─ mandatory memory/recall(query)
       ↓
       vector-similar node candidates
       ↓
Agent round 2+
  ├─ memory/recall(node_id) → add node + one-hop to ViewedGraph
  ├─ memory generate/fix → immediate SQLite commit
  ├─ file/web/other work tool
  └─ answer only after graph-sync confirmation
```

Each explicit Agent round maps to exactly one structured model request.
No hidden model retry/review/fallback request may be inserted by the model adapter.

Graph mutations are not deferred until after the user-facing answer.
An accepted mutation is committed immediately and becomes available to later rounds in the same turn and to future turns.

Consequently, an incorrect intermediate model judgment may remain durable if the turn later fails before the model corrects it. This is an intentional consequence of using the persistent graph as live working memory rather than a transactional post-answer store.

---

## 2. Mandatory vector recall and the recent-dialogue boundary

Recent raw conversation remains a small context window. Long-term memory is not automatically dumped into every model request.

The first Agent round of every turn must perform `memory/recall` with a semantic query before `answer` becomes an available action. This is a framework-enforced protocol state, not a prompt-only suggestion.

The query recall uses an embedding model configured through `.env` and returns several vector-similar node candidates. Candidate retrieval does not automatically inject every candidate neighborhood into model context.
The model chooses which candidate to open.

`memory/recall` therefore has two modes:

1. `query`: embedding/vector-similarity candidate search;
2. `node_id`: open one selected/known node and exactly one hop of active relationships.

When information may exist outside the recent dialogue, the model must continue using memory recall before concluding that it does not remember or does not know the relevant past user context.
The framework does not infer memory intent from string heuristics.

The embedding model is runtime configuration, not a semantic hard-coded rule. The reference configuration uses:

```text
MAI_OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```

Embedding failure is surfaced as a real failure. Lexical search is not used as a silent fallback for vector recall.

---

## 3. Turn-scoped ViewedGraph

Every turn owns an in-memory `ViewedGraph` representing the portion of the persistent graph the Agent has explicitly opened during that turn.

Initially the ViewedGraph is empty. Vector candidate search does not itself add neighborhoods to it.
When the model opens a node through `memory/recall(node_id)`, that node and its active one-hop neighborhood are merged into the ViewedGraph.

Subsequent recalls accumulate rather than replace prior results:

```text
recall node A
→ ViewedGraph = A + A one-hop

recall node B
→ ViewedGraph = previous ViewedGraph ∪ B + B one-hop
```

The current ViewedGraph is returned with memory operations so later Agent rounds continue to see the graph accumulated so far.
Memory generate/fix operations refresh affected portions so the ViewedGraph follows the graph's newly committed state rather than retaining stale copies.

The ViewedGraph is only a turn-scoped view/index over the real persistent graph. It is not a second memory database and it is never promoted after the answer.
At turn completion or failure, the ViewedGraph is discarded. Persistent graph mutations remain committed.

---

## 4. Final graph-sync gate

The design expects the Agent to keep the persistent graph synchronized while its understanding develops, rather than running a second post-answer memory phase.

Before returning the final `answer`, the same Agent must explicitly assert that the graph state it has worked with is aligned with its latest understanding of durable information from the turn.
If it is not aligned, the Agent must use `memory/generate/*` or `memory/fix/*` in another normal Agent round before answering.

This is a thin termination gate inside the Agent loop. It does not create a second model role or a hidden review call.
The framework does not itself semantically compare the answer text with graph contents.

---

## 5. Graph model

### 5.1 Node

A node represents one semantic entity or concept.

Model-facing shape should expose at least:

```text
Node
- node_id
- name / semantic label
- kind
- source_ids[]
```

Supported node kinds include ordinary semantic concepts and composite concepts.

A composite node represents a model-declared concept whose meaning is constituted by multiple existing nodes.
Composite membership is framework-owned structural data, not an ordinary model-authored relation string.
Composite nodes may participate in normal semantic edges like any other node.
Structural self-membership and composite membership cycles are invalid.

### 5.2 Edge

An edge is a directed current relationship state between exactly two nodes.
Direction is represented by the endpoints themselves; a separate direction flag is unnecessary.

```text
Edge
- edge_id
- start_node_id
- end_node_id
- relation
- weight
- personal_relevance
- source_ids[]
```

For one user graph, only one active/current edge may exist for a given ordered endpoint pair:

```text
(start_node_id, end_node_id)
```

Therefore A -> B and B -> A are distinct and may both exist, but multiple parallel A -> B semantic edges are not created merely because relation wording differs.

The relation field is the current integrated description of the relationship between the two nodes. New information about the same directed pair should normally fix the existing edge rather than create another edge.

---

## 6. Reuse-first memory policy

Node and edge reuse/fix are preferred over generation.

### 6.1 Node generation

Before a new semantic node may be generated, the agent must perform a relevant `memory/recall(query)` during the current turn so existing vector-similar candidates can be considered.

The framework enforces the prior-recall requirement.
The framework does not decide semantic equivalence using string containment, aliases, hard-coded dictionaries, or other text heuristics.

The model decides whether a recalled candidate is semantically the same concept.
If it is the same concept, the existing `node_id` must be reused.
If no candidate represents the intended meaning, a new node may be generated.

New-node budget:

- at most 10 newly created nodes per turn;
- composite nodes consume the same new-node budget;
- recalled/existing nodes do not consume the budget;
- exceeding the budget is a visible contract error, not a silent success or fallback.

### 6.2 Duplicate repair

Because semantic duplicate prevention cannot be perfect, `memory/fix/node` supports merging a duplicate node into a selected canonical node.

A merge moves or reconciles structural references, source links, semantic edges, and composite membership onto the canonical node while preserving graph ownership constraints.
If a merge would require a semantic choice between conflicting active edges, the framework fails visibly instead of silently choosing one; the Agent must fix/disconnect the conflict first.

---

## 7. Edge generation and fixing

### 7.1 Generate edge

`memory/generate/edge` creates a directed edge only when the ordered pair does not already have the current edge.

If the pair already exists, generation is rejected visibly and the existing edge identifier is returned so the model can use `memory/fix/edge`.
There is no fallback that silently converts generate into fix.

### 7.2 Fix edge

`memory/fix/edge` is the normal path for changing an existing relationship.
It can update the relation state, source associations, personal relevance, and weight.

Weight changes are expressed as a delta against the existing value rather than requiring the model to replace an unknown current value blindly.
The framework applies the delta and clamps the resulting weight to the structural range 0.0 through 1.0.

Disconnecting a relationship is performed through `memory/fix/edge` by making its weight `0`.
A zero-weight edge is retained for provenance/debugging but is not treated as an active semantic connection by ordinary one-hop recall.

The abandoned design of stacking up to three historical edge versions is not used.

### 7.3 Edge mutation budget

There is no permanent degree limit on a node.
A node may accumulate arbitrarily many relationships over its lifetime.

For one turn, each node may participate in at most 10 semantic edge mutations.
An edge mutation counts against both participating nodes.
This is a per-turn execution budget, not a permanent graph-capacity limit.

---

## 8. Weight and personal relevance

`weight` and `personal_relevance` are distinct concepts.

`weight` represents the current strength of the directed relationship itself.
It may be strengthened or weakened through `memory/fix/edge` using a delta.
A weight of zero represents a disconnected/inactive semantic relationship.

`personal_relevance` represents how directly the memory concerns the user, independently of source reliability and independently of edge strength.

The Agent chooses one structural classification and the framework maps it to:

```text
user_centered      -> 1.0
general_knowledge  -> 0.5
```

The framework must not determine this classification from keywords or other semantic string rules.
Repeated evidence may promote relevance from 0.5 to 1.0; a lower-relevance observation does not automatically downgrade an already user-centered relationship.

---

## 9. Sources and provenance

The graph keeps current semantic state, while provenance keeps supporting evidence.

Model-facing nodes and edges expose source references as arrays:

```text
source_ids: [12, 18, 44]
```

SQLite uses relational source/link tables rather than opaque JSON arrays. Both nodes and edges have independent provenance because node existence and a relationship claim are different assertions.

Historical semantic edge versions are not required for normal operation. Source/provenance history remains available for later inspection and repair.

---

## 10. Memory tool namespace

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

`memory/generate` and `memory/fix` are namespace/menu nodes rather than executable mutations.

### memory/recall

- `query`: vector-similar candidate nodes;
- `node_id`: selected node + exactly one active hop;
- node-id recall merges its neighborhood into the current ViewedGraph;
- may be reused at any Agent round;
- excludes zero-weight semantic edges from ordinary active recall.

### memory/generate/node

- generate a new semantic or composite node;
- requires a query recall first;
- new-node budget applies;
- semantic duplicate candidates must be reused when the model judges them equivalent.

### memory/generate/edge

- create one directed relationship state for an ordered node pair;
- refuses an already-existing ordered pair and points the model to the existing edge.

### memory/fix/node

- update an existing node;
- merge a duplicate node into a canonical node;
- update composite membership where structurally valid.

### memory/fix/edge

- update current relation state;
- apply `weight_delta`;
- update/promote personal relevance;
- add source support;
- disconnect by setting resulting weight to zero.

---

## 11. Tool discovery hierarchy

Large external work-tool schemas are not all exposed at the beginning of every Agent round.
The initial external catalog is a small namespace-level view.

Conceptually:

```text
memory
file
web
answer (after mandatory query recall)
```

Memory remains a core Agent capability. External work tools use lazy hierarchical discovery.

### File namespace

Representative protocol:

```text
/file
/file/tree
/file/tree/manual
/file/tree/use
```

A model that already understands a tool during the current Agent loop may address the exact `/.../use` route directly without reopening the manual.
Paths are parsed as registered structural route segments. Unknown paths return a visible structured error and valid child choices; the framework does not guess or autocorrect them.

### Web namespace

```text
/web/search   - ordinary web search
/web/market   - market/equity/index/FX data
/web/current  - current/latest information
```

Human-facing descriptions may be localized, but routing identifiers are stable structural identifiers.

---

## 12. Immediate persistence

Every accepted graph mutation commits to the real database immediately.

```text
Agent round N
  -> memory/generate or memory/fix
  -> SQLite commit

Agent round N+1
  -> memory/recall or current ViewedGraph
  -> observes that mutation
```

The same mutation is recallable in future turns.
There is no temporary scratchpad graph and no post-answer promotion step.

---

## 13. Failure rules

Failures remain visible.

- no lexical fallback when embedding recall fails;
- no string-based semantic fallbacks;
- no guessed tool route on an invalid path;
- no silent generate-to-fix conversion;
- no silent duplicate-node creation after missing the required recall;
- no hidden model re-request in the adapter;
- structural scope/ownership/self-reference/cycle/budget violations raise explicit contract errors;
- tool and OS failures are surfaced as actual failures.

The framework owns structural validity. The model owns semantic decisions.

---

## 14. Simplifications from the previous runtime

The runtime removes or retires responsibilities that existed only for the dedicated post-answer memory phase:

- dedicated Qwen memory model orchestration;
- `GraphCommitPhase` as a second top-level model loop;
- `continue_memory` protocol;
- post-answer memory mutation phase;
- request-scoped scratchpad tools and scratchpad registry plumbing;
- scratchpad-to-memory selection as an independent mechanism.

Useful source/provenance infrastructure remains but attaches directly to Agent-observed evidence and graph mutations.

The final runtime objective is one explicit Agent loop in which vector recall, turn-scoped ViewedGraph, persistent graph mutation, external tools, and final answering are all first-class actions.