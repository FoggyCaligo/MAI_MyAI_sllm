# MAI Memory v1

This document is the implementation contract for MAI's long-term memory. Memory v1 keeps the parts of MACHI MK4 that produced the most interpretable answers — user anchors, directly addressable utterance evidence, fact nodes, reusable concept nodes, typed edges, and provenance — while replacing MK4's custom relevance/activation entry path with a replaceable vector index.

## 1. Core idea

Sentence_Breaker defines reusable **concept-node boundaries**, not the entire memory graph.

```text
User utterance
  -> immutable raw Evidence
  -> after final response only:
       Utterance Node (original sentence)
       Fact Nodes (model-extracted user facts)
       Concept Nodes (Sentence_Breaker segments)
```

Concept identity is exact segment identity:

```text
one canonical Sentence_Breaker segment = one Concept Node = one vector
```

Repeated appearances of the same segment reuse the same concept and vector. Semantic similarity is used only to retrieve an existing concept; it never merges two concept identities.

The full original sentence remains a first-class Utterance Node. This is deliberate: recall must be able to show the model what the user actually said instead of forcing it to trust a free-form rewritten relation description.

## 2. Permanent graph node types

### User Anchor

Every user account owns one persistent anchor:

```text
user_anchor::<user_id>
```

The anchor is a real permanent graph node but is never vector-indexed. It establishes whose memory a recalled subgraph belongs to.

### Utterance Node

An Utterance Node contains the original user sentence and an immutable `evidence_id` in its payload.

```text
User Anchor
    └─spoke→ Utterance
```

Utterances are evidence-bearing graph nodes and may be returned directly during recall.

### Fact Node

A Fact Node is a concise long-term fact extracted by a model **after the final response has been accepted**. It does not replace its source utterance.

```text
User Anchor ─asserted_fact→ Fact
Utterance   ─derived_fact─→ Fact
```

Fact extraction is the model-written semantic layer. Edge meanings are not freely rewritten by the model.

### Concept Node

A Concept Node is an exact Sentence_Breaker segment. Concept Nodes are globally reusable and are the only graph nodes stored in the vector index.

```text
Utterance ─mentions→ Concept
Fact      ─mentions→ Concept
```

## 3. Typed edges and provenance

Edges use runtime-defined relation types rather than model-written relation prose. The initial Memory v1 vocabulary is intentionally small:

```text
user_anchor -> utterance : spoke
user_anchor -> fact      : asserted_fact
utterance   -> fact      : derived_fact
utterance   -> concept   : mentions
fact        -> concept   : mentions
```

Each edge also stores provenance such as `user_utterance`, `user_assertion`, `derived_from_utterance`, or `fact_index`.

The database enforces one edge per `(from_node_id, to_node_id, relation)`. The same pair may have different typed relations, but an identical typed relation is not duplicated.

This replaces the earlier proposed `A -> B + latest three model-written relation descriptions` design. That representation was compact but placed model interpretation ahead of the original sentence, which can make remembered answers less stable. Memory v1 instead preserves the source sentence as a node and uses facts as a derived, traceable layer.

## 4. Evidence

Raw user input is stored in the immutable `evidence` table before the agent run so the original source cannot be lost. Recording raw evidence is not semantic graph mutation.

After the final answer, an Utterance Node is created for that evidence and connected to the user anchor and derived memory nodes.

Therefore the system can answer both:

```text
What do I currently remember?
```

and:

```text
Why do I remember that? What did the user actually say?
```

without treating a rewritten graph relation as the original source.

## 5. Tool requirement preflight comes before recall

Tool requirement planning happens **before automatic recall**.

This ordering is mandatory. If automatic memory, web results, or file results are visible first, a small model can conclude that the request is already sufficiently answered and incorrectly mark `memory_search` or `web_search` as unnecessary.

```text
User input
  -> Tool Requirement Preflight
       current request
       minimum recent dialogue needed to resolve the request
       available capabilities
       NO auto-recall
       NO Working Graph
       NO search/tool results
  -> freeze required tool obligations
  -> automatic memory recall
  -> main Ollama-native agent loop
  -> verify frozen obligations
  -> final response accepted
  -> post-response memory update
```

`required=true` means a successful call is mandatory before final-answer acceptance. `required=false` means only that there is no preflight obligation; the agent remains free to use that tool later.

## 6. sqlite-vec and the VectorIndex boundary

Memory v1 uses `sqlite-vec` as the first concrete vector backend. Graph tables and vector tables can live in the same `memory.db` file.

```text
memory.db
  nodes
  user_anchors
  evidence
  edges
  memory_node_vectors   <- sqlite-vec vec0 virtual table
```

Only Concept Nodes are inserted into `memory_node_vectors`, and `rowid` is the permanent graph node ID.

```text
Concept Node id 3817
      ↕
memory_node_vectors.rowid 3817
```

The memory core does **not** depend directly on sqlite-vec. It depends on the `VectorIndex` protocol:

```text
Memory Runtime
    -> VectorIndex
         -> SqliteVecIndex today
         -> another backend later if needed
```

This boundary is permanent. sqlite-vec may be replaced without changing graph identity or recall semantics.

Embeddings are generated through an independent `EmbeddingProvider` boundary. The initial local implementation uses Ollama `/api/embed`.

## 7. Automatic recall and the Working Graph

The vector index is only the semantic entry point. A vector hit is a Concept Node, not a final memory answer.

For each concept hit, automatic recall adds:

1. the concept's one-hop neighborhood, which can expose related Fact and Utterance Nodes;
2. the shortest available graph path from the hit back to the current user's account anchor.

The shortest-path search ignores edge direction only while discovering topology. Returned edges preserve their real stored direction, relation type, and provenance.

```text
Current user input
  -> Sentence_Breaker query segments
  -> sqlite-vec searches Concept Nodes
  -> concept seed
  -> seed one-hop
  -> seed -> current user anchor shortest path
  -> merge union into Working Graph
```

This prevents isolated fragments such as `MAI -> project` from being shown without enough structure to establish whose project or memory it is.

The Working Graph is temporary per-turn state. It is not persisted as another graph.

## 8. Deliberate memory expansion

`memory_search(node_id)` expands exactly one permanent-graph hop and merges that neighborhood into the current Working Graph. Newly visible nodes also receive their available shortest paths back to the current user's anchor.

```text
Working Graph
  concept -> utterance -> user anchor

model calls memory_search(concept)
    -> one-hop permanent neighborhood
    -> merge facts/utterances/concepts/typed edges
    -> preserve user-root paths
    -> next model round sees expanded Working Graph
```

There is no arbitrary-depth hidden traversal. The model recalls farther by calling `memory_search` again. Agent guards therefore also bound deliberate memory traversal.

## 9. Post-response semantic update

No interpreted graph memory is written during the native tool-use loop.

```text
raw user evidence saved
  -> preflight
  -> auto-recall
  -> agent/tool loop
  -> final answer accepted
  -> agent loop ends
  -> MemoryRuntime.finish_turn()
       create Utterance Node
       connect user_anchor -> utterance (spoke)
       Sentence_Breaker concepts from utterance
       connect utterance -> concept (mentions)
       model extracts user facts
       create/reuse Fact Nodes
       user_anchor -> fact (asserted_fact)
       utterance -> fact (derived_fact)
       fact -> concepts (mentions)
       add vectors only for newly-created Concept Nodes
```

The future fact-extraction model must extract only facts actually grounded in the user's utterance when those facts are stored as user assertions. Tool/search-derived world facts require their own evidence-bearing node/source policy rather than being silently attributed to the user.

## 10. Failure semantics

Memory follows the same MAI rules as the agent runtime:

- no string-contains routing;
- no semantic vector similarity for identity merging;
- no silent replacement of failed required tools;
- no rewriting source utterances;
- no free-form model edge meaning used as evidence;
- schema, SQLite, embedding, and sqlite-vec failures remain explicit;
- a required `memory_search` that did not successfully run cannot be considered satisfied.

## 11. Implementation boundaries

```text
mai/memory/
  graph/
    schema.py       typed permanent graph schema
    models.py       anchor/utterance/fact/concept/edge values
    repository.py   graph identity, typed edges, one-hop and anchor paths
  vector/
    index.py        replaceable VectorIndex protocol
    embedding.py    replaceable EmbeddingProvider + Ollama implementation
    sqlite_vec.py   sqlite-vec backend
  working.py        per-turn Working Graph
  segmenter.py      Sentence_Breaker adapter
  recall/           vector entry + one-hop + user-anchor path assembly
  extraction/       post-response user FactExtractor contract
  runtime.py        memory lifecycle coordinator
  tools.py          user-bound native memory_search
```

The design goal is therefore not "vector memory instead of graph memory". It is:

> **MK4-style evidence/provenance graph memory, with sqlite-vec used only to find the right reusable concept nodes quickly.**
