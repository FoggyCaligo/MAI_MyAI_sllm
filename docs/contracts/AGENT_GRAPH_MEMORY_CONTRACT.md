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
Agent round
  ├─ memory recall
  ├─ memory generate/fix
  ├─ file/web/other work tool
  └─ answer
  ↓
next Agent round as needed
```

Each explicit Agent round maps to exactly one structured model request.
No hidden model retry/review/fallback request may be inserted by the model adapter.

Graph mutations are not deferred until after the user-facing answer.
An accepted mutation is committed immediately and becomes available to later rounds in the same turn and to future turns.

Consequently, an incorrect intermediate model judgment may remain durable if the turn later fails before the model corrects it. This is an intentional consequence of using the persistent graph as live working memory rather than a transactional post-answer store.

---

## 2. Recall and the recent-dialogue boundary

Recent raw conversation remains a small context window. Long-term memory is not automatically injected into every model request.

When information may exist outside the recent dialogue, the model must use memory recall before concluding that it does not remember or does not know the relevant past user context.

This is an agent behavior contract, not a text-pattern fallback.
The framework does not infer memory intent from string heuristics.

`memory/recall` supports two access modes:

1. semantic/similarity association from a model-authored query;
2. direct access by a known `node_id`.

Both modes return one hop only.
The model may call `memory/recall` again from a returned node when more traversal is useful.
The framework does not automatically expand arbitrary multi-hop neighborhoods.

---

## 3. Graph model

### 3.1 Node

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

### 3.2 Edge

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

## 4. Reuse-first memory policy

Node and edge reuse/fix are preferred over generation.

### 4.1 Node generation

Before a new semantic node may be generated, the agent must perform a relevant `memory/recall` lookup during the current turn so that existing candidates can be considered.

The framework enforces the prior-lookup requirement.
The framework does not decide semantic equivalence using string containment, aliases, hard-coded dictionaries, or other text heuristics.

The model decides whether a recalled candidate is semantically the same concept.
If it is the same concept, the existing `node_id` must be reused.
If no candidate represents the intended meaning, a new node may be generated.

New-node budget:

- at most 10 newly created nodes per turn;
- composite nodes consume the same new-node budget;
- recalled/existing nodes do not consume the budget;
- exceeding the budget is a visible contract error, not a silent success or fallback.

### 4.2 Duplicate repair

Because semantic duplicate prevention cannot be perfect, `memory/fix/node` must support merging a duplicate node into a selected canonical node.

A merge moves or reconciles structural references, source links, semantic edges, and composite membership onto the canonical node while preserving graph ownership constraints.
The duplicate node is no longer used as an independent current semantic node after the merge.

---

## 5. Edge generation and fixing

### 5.1 Generate edge

`memory/generate/edge` creates a directed edge only when the ordered pair does not already have the current edge.

If the pair already exists, generation is rejected visibly and the existing edge identifier is returned so the model can use `memory/fix/edge`.
There is no fallback that silently converts generate into fix.

### 5.2 Fix edge

`memory/fix/edge` is the normal path for changing an existing relationship.
It can update the relation state, source associations, personal relevance, and weight.

Weight changes are expressed as a delta against the existing value rather than requiring the model to replace an unknown current value blindly.
The framework applies the delta and enforces the configured numeric range.

Disconnecting a relationship is performed through `memory/fix/edge` by making its weight `0`.
A zero-weight edge is retained for provenance/debugging but is not treated as an active semantic connection by ordinary one-hop recall.

The current graph therefore represents current active knowledge, while provenance preserves why that state exists.
The abandoned design of stacking up to three historical edge versions is not used.

### 5.3 Edge mutation budget

There is no permanent degree limit on a node.
A node may accumulate arbitrarily many relationships over its lifetime.

For one turn, however, each node may participate in at most 10 semantic edge mutations.
An edge mutation counts against both participating nodes because it is a relationship of both endpoints.
This is a per-turn execution budget, not a permanent graph-capacity limit.

---

## 6. Weight and personal relevance

`weight` and `personal_relevance` are distinct concepts.

### 6.1 Weight

`weight` represents the current strength of the directed relationship itself.
It may be strengthened or weakened through `memory/fix/edge` using a delta.
A weight of zero represents a disconnected/inactive semantic relationship.

### 6.2 Personal relevance

`personal_relevance` represents how directly the memory concerns the user, independently of source reliability and independently of edge strength.

The memory model/agent chooses one structural classification and the framework maps it to the fixed relevance multiplier:

```text
user_centered      -> 1.0
general_knowledge  -> 0.5
```

The framework must not determine this classification from keywords or other semantic string rules.

When repeated evidence would raise the relevance of an existing edge, relevance may be promoted from 0.5 to 1.0.
A later lower-relevance observation does not automatically downgrade an already user-centered relationship.

Source reliability/confidence remains a separate concept and must not be conflated with personal relevance.

---

## 7. Sources and provenance

The graph keeps current semantic state, while provenance keeps the supporting evidence.

Model-facing nodes and edges may expose source references as simple arrays:

```text
source_ids: [12, 18, 44]
```

SQLite should not store those arrays as opaque JSON fields when normal relational links are available.
Use source/link tables so one source may support multiple nodes/edges and duplicate links can be constrained structurally.

At minimum:

```text
graph_sources
node_sources (or graph_source_links targeting a node)
edge_sources (or graph_source_links targeting an edge)
```

A source record preserves the evidence unit and its turn/source metadata.
Both nodes and edges require independent provenance because the existence of two concepts and the claim that they are related are different assertions.

Historical semantic edge versions are not required for normal operation.
Source/provenance history is retained so an incorrect current graph state can later be understood and repaired.

---

## 8. Memory tool namespace

The model-facing memory namespace is intentionally small:

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

`memory/generate` and `memory/fix` are namespace/menu nodes rather than executable mutations by themselves.

### memory/recall

- semantic/similarity query, or direct `node_id` access;
- exactly one-hop result;
- reusable at any Agent round;
- excludes zero-weight semantic edges from ordinary active recall.

### memory/generate/node

- generate a new semantic or composite node;
- requires a relevant recall first;
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

## 9. Tool discovery hierarchy

Large work-tool schemas are not all exposed at the beginning of every Agent round.
The initial catalog is a small namespace-level view.

Conceptually:

```text
memory
file
web
answer
```

Memory remains a core Agent capability because the graph is both working memory and long-term memory.
External work tools use lazy hierarchical discovery.

### 9.1 File namespace

The file namespace exposes children such as tree/search/read/create/update/delete/document/image rather than dumping all schemas initially.

Representative protocol:

```text
/file
/file/tree
/file/tree/manual
/file/tree/use
```

A model that already understands a tool during the current Agent loop may address the exact `/.../use` route directly without reopening the manual.

The path is parsed as registered structured route segments, not interpreted with substring heuristics.
An unknown path returns a visible structured error and valid child choices.
The framework does not guess or autocorrect the intended route.

### 9.2 Web namespace

The web namespace uses the same mechanism, with categories corresponding to:

```text
/web/search   - ordinary web search
/web/market   - market/equity/index/FX data
/web/current  - current/latest information
```

Human-facing descriptions may be localized, but protocol identifiers are stable structural identifiers and routing must not depend on translated text.

### 9.3 Manual versus direct use

For a leaf tool, the agent may request its manual/schema first or request direct use when it already knows the contract.
The exact leaf schema is exposed only when needed.

This hierarchy is intended to reduce schema/context load on small local models without hiding failures or introducing semantic routing heuristics.

---

## 10. Immediate persistence and graph-as-working-memory behavior

Every accepted graph mutation commits to the real database immediately.

Therefore:

```text
Agent round N
  -> memory/generate or memory/fix
  -> SQLite commit

Agent round N+1
  -> memory/recall
  -> observes that mutation
```

The same mutation is also recallable in future turns.
There is no separate temporary scratchpad graph and no post-answer promotion step in this target runtime.

The agent is expected to use recall and fix repeatedly as its understanding changes.
If it discovers that an earlier committed relation is wrong, it should repair the current graph using `memory/fix/*` rather than relying on a hidden temporary state.

---

## 11. Failure rules

Failures remain visible.

- no string-based semantic fallbacks;
- no guessed tool route on an invalid path;
- no silent generate-to-fix conversion;
- no silent duplicate-node creation after missing the required recall;
- no hidden model re-request in the adapter;
- structural scope/ownership/self-reference/cycle/budget violations raise explicit contract errors;
- tool and OS failures are surfaced as actual failures.

The framework owns structural validity.
The model owns semantic decisions.

---

## 12. Expected simplifications from the previous runtime

The target implementation should remove or retire runtime responsibilities that only existed for the dedicated post-answer memory phase:

- dedicated Qwen memory model orchestration;
- `GraphCommitPhase` as a second top-level model loop;
- `continue_memory` protocol;
- post-answer memory mutation phase;
- request-scoped scratchpad tools and scratchpad registry plumbing;
- scratchpad-to-memory selection as an independent mechanism.

Useful source/provenance infrastructure may remain, but it should attach directly to Agent-observed evidence and graph mutations rather than requiring a scratchpad intermediary.

The final runtime objective is one explicit Agent loop in which tools, graph recall, graph mutation, and final answering are all first-class actions.
