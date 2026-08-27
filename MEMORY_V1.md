# MAI Memory v1

This document is the implementation contract for the new MAI long-term memory. It intentionally does **not** copy MK4's memory implementation. It keeps the useful ideas (reusable concept nodes, provenance, deliberate memory search, and frozen tool obligations) while replacing the old recall/scoring pipeline.

## 1. Core idea

MAI stores long-term memory as a graph whose basic node is **not a whole sentence**. User text is first segmented by the reusable [`FoggyCaligo/Sentence_Breaker`](https://github.com/FoggyCaligo/Sentence_Breaker) package. A segment becomes a reusable memory node.

The invariant is:

```text
one canonical segment text = one Node = one vector
```

Repeated appearances of the same segment reuse the existing node and vector. Vector search is therefore used to jump quickly to relevant unique nodes without storing a new sentence-sized vector for every utterance.

```text
User text
  -> Sentence_Breaker
  -> segment[]
  -> exact canonical-node lookup
       -> existing node: reuse
       -> new node: create once + embed once
```

Semantic vector similarity is **never** used to merge node identity. Similar words may be close in vector space but remain distinct nodes.

## 2. Permanent graph

A permanent graph contains Nodes, directed Edges, relation observations, and immutable Evidence.

### Node

A Node represents one canonical Sentence_Breaker segment. Node identity is exact canonical text identity. Each Node has one vector in the vector index.

### Directed Edge

For an ordered pair there can be at most one edge:

```text
UNIQUE(from_node_id, to_node_id)
```

Therefore `A -> B` and `B -> A` may each exist once, but parallel `A -> B` edges are forbidden.

### Relation observation queue

The model may describe what an edge means, but it does not rewrite history. Every edge owns a newest-first queue of at most three relation observations:

```text
A -> B
  [0] newest relation detail + timestamp + evidence_id
  [1] previous relation detail + timestamp + evidence_id
  [2] oldest retained relation detail + timestamp + evidence_id
```

A fourth observation pushes out the oldest retained interpretation. Timestamps and evidence IDs are attached by the runtime, not invented by the model.

The queue is a compact history of the **latest interpretations**. It is not the evidence store.

### Evidence

Raw source evidence is immutable and independent from the three-item relation queue. Removing an old relation observation from the queue must not delete the original user utterance/tool evidence. This prevents the model from changing a relation description and then treating the rewritten description as the original source.

## 3. Turn order and frozen tool requirements

Tool requirement planning happens **before automatic recall**.

This ordering is mandatory. If automatic recall, web results, or file results are shown first, a small model can decide that it already has enough information and incorrectly mark `memory_search` or `web_search` as unnecessary.

```text
User input
  -> Tool Requirement Preflight
       input: user request + minimum conversational context + capability list
       no auto-recall
       no working graph
       no search/tool results
  -> freeze required tool capabilities
  -> automatic memory recall
  -> main agent loop
  -> verify every frozen requirement was satisfied
  -> final response
  -> post-response semantic memory update
```

`required=true` means at least one successful call of that capability is mandatory before a final answer may be accepted. `required=false` does **not** prohibit use; the main agent may still call any available tool discovered to be useful later.

The frozen set is an obligation set, not an allowlist.

## 4. Automatic recall and Working Graph

Vector search is the fast entry point into memory. It is used to find relevant unique nodes. The initial Working Graph contains vector hits and their one-hop permanent-graph neighborhood.

```text
Current user input
  -> Sentence_Breaker
  -> vector queries
  -> relevant entry Nodes
  -> permanent graph 1-hop expansion
  -> initial Working Graph
```

The Working Graph is temporary per-turn cognitive state. It is not another permanent database and it is not written back as a whole.

Automatic recall is deliberately shallow: **one hop**.

## 5. Deliberate memory expansion

`memory_search` is a native agent tool. It expands a selected node by exactly one permanent-graph hop. Returned nodes, edges, relation observations, and evidence references are merged into the current Working Graph. The next model round sees the expanded Working Graph.

```text
Working Graph
 A -> B -> C

model calls memory_search(B)
        -> Permanent Graph returns B's one-hop neighborhood
        -> merge new nodes/edges into Working Graph

model calls memory_search(E)
        -> another one-hop expansion
```

There is intentionally no automatic arbitrary-depth traversal. Deeper recall is expressed as repeated explicit one-hop searches. This makes the path of deliberate recall observable and bounded by the normal agent loop/guards.

## 6. Vector DB responsibility

Vector search and graph traversal have separate jobs:

```text
Vector index = jump to a semantically relevant place
Permanent graph = stored relationships and evidence
Working Graph = the part currently in mind
memory_search = deliberately look one hop farther
```

The vector backend must enforce one vector per unique Node. The memory core talks to it through a narrow interface so the concrete vector database can be replaced without changing graph semantics.

## 7. Post-response memory update

Semantic graph mutation must never run during the tool-use loop.

```text
main agent/tool loop
  -> final response accepted
  -> loop ends
  -> PostTurnMemoryWriter runs once
  -> model proposes semantic relations
  -> runtime attaches real timestamp/evidence IDs
  -> permanent graph transaction commits
```

Raw user evidence may be recorded before the agent run so the original utterance cannot be lost, but interpreted semantic relations are written only after the final response.

The relation-writing model may propose `from`, `to`, and `relation_detail`. It must not choose timestamps, rewrite evidence, create parallel edges, or bypass the three-observation queue.

## 8. Failure semantics

Memory follows the same MAI rules as the agent runtime:

- no string-contains routing;
- no semantic identity merging by heuristic;
- no silent fallback from a failed required tool;
- no rewriting evidence to make a relation appear supported;
- schema/DB/vector failures remain explicit failures;
- a required memory search that did not successfully run cannot be treated as satisfied.

## 9. Implementation boundaries

```text
mai/memory/
  graph/        permanent SQLite graph and contracts
  vector/       vector-index interface/backends
  working.py    per-turn Working Graph
  segmenter.py  Sentence_Breaker adapter
  recall/       auto-recall and one-hop expansion
  extraction/   post-response relation proposals
  runtime.py    memory lifecycle coordinator
  tools.py      native memory_search adapter
```

The concrete vector database and embedding model are replaceable infrastructure. Node/edge/evidence identity and turn ordering are Memory v1 invariants.