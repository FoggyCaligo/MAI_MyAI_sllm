# MAI Memory v1

This document is the implementation contract for MAI's long-term memory. Memory v1 keeps the MACHI MK4 properties that produced interpretable answers — user anchors, directly addressable utterance evidence, fact nodes, reusable concept nodes, typed edges, and provenance — while using a model-independent ConceptIndex for recall entry.

## 1. Core idea

Sentence_Breaker defines reusable **Concept Node boundaries**, not the entire memory graph.

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
one canonical Sentence_Breaker segment = one Concept Node
```

Repeated appearances of the same segment reuse the same Concept Node. Recall indexing never merges graph identity.

The full original sentence remains a first-class Utterance Node so recall can show the model what the user actually said instead of forcing it to trust a rewritten relation description.

## 2. Permanent graph node types

### User Anchor

Every user account owns one persistent anchor:

```text
user_anchor::<user_id>
```

The anchor is a permanent graph node but is never inserted into the ConceptIndex.

### Utterance Node

An Utterance Node contains the original user sentence and an immutable `evidence_id`.

```text
User Anchor
    └─spoke→ Utterance
```

### Fact Node

A Fact Node is a concise long-term fact extracted after the final response has been accepted. It never replaces its source utterance.

```text
User Anchor ─asserted_fact→ Fact
Utterance   ─derived_fact─→ Fact
```

### Concept Node

A Concept Node is an exact Sentence_Breaker segment. Concept Nodes are globally reusable and are the only graph nodes placed in the ConceptIndex.

```text
Utterance ─mentions→ Concept
Fact      ─mentions→ Concept
```

## 3. Typed edges and provenance

Runtime-defined relation types are used instead of model-written relation prose:

```text
user_anchor -> utterance : spoke
user_anchor -> fact      : asserted_fact
utterance   -> fact      : derived_fact
utterance   -> concept   : mentions
fact        -> concept   : mentions
```

Each edge stores provenance such as `user_utterance`, `user_assertion`, `derived_from_utterance`, or `fact_index`.

The database enforces one edge per `(from_node_id, to_node_id, relation)`.

## 4. Evidence

Raw user input is stored in the immutable `evidence` table before the agent run. Recording raw evidence is not semantic graph mutation. After the final answer, an Utterance Node is created for that evidence and connected to the user anchor and derived memory nodes.

This allows both semantic recall and direct inspection of the original source sentence.

## 5. Tool requirement preflight comes before recall

Tool requirement planning happens **before automatic recall**.

```text
User input
  -> Tool Requirement Preflight
       current request
       minimum recent dialogue
       available capabilities
       NO auto-recall / Working Graph / search results
  -> freeze required tool obligations
  -> automatic memory recall
  -> main Ollama-native agent loop
  -> verify frozen obligations
  -> final response accepted
  -> post-response memory update
```

`required=true` means a successful call is mandatory before final-answer acceptance. `required=false` does not prohibit later tool use.

## 6. Model-independent ConceptIndex: Exact + SQLite FTS5

Memory v1 does not require embeddings or a vector database. Persistent recall entry is handled by a `ConceptIndex` boundary whose first concrete implementation is `SqliteFtsConceptIndex`.

```text
memory.db
  nodes
  user_anchors
  evidence
  edges
  memory_concept_exact
  memory_concept_fts
```

`memory_concept_exact` persists the exact mapping from canonical Concept text to permanent Concept Node ID. At runtime this mapping is loaded into an in-memory Python dictionary, providing exact hash lookup.

`memory_concept_fts` is an SQLite FTS5 virtual table used only as a lexical fallback when exact lookup does not find a Concept. It does not perform embedding similarity and does not define graph identity.

```text
Sentence_Breaker query segments
        ↓
Exact hash lookup
        ↓ miss
SQLite FTS5 lexical search
        ↓
Concept Node IDs
```

The graph owns identity. The index only locates existing Concept Node IDs.

The memory core depends on the `ConceptIndex` protocol:

```text
Memory Runtime
    -> ConceptIndex
         -> SqliteFtsConceptIndex
```

There is no embedding model configuration and no model-specific vector space. Changing the main LLM or memory-writing LLM therefore does not require rebuilding long-term memory or recall coordinates.

When `SqliteFtsConceptIndex` opens an existing Memory v1 database, it non-destructively synchronizes any existing permanent Concept Nodes that are not yet present in the exact/FTS tables. Legacy sqlite-vec tables, if present from an older development database, are ignored rather than silently deleted.

## 7. Automatic recall and the Working Graph

The ConceptIndex is only the recall entry point. A Concept hit is not a final memory answer.

For each hit, automatic recall adds:

1. the Concept's one-hop neighborhood, exposing related Fact and Utterance Nodes;
2. the shortest available graph path from the hit back to the current user's account anchor.

Shortest-path discovery treats topology as undirected, while returned edges preserve stored direction, relation, and provenance.

```text
Current user input
  -> Sentence_Breaker query segments
  -> Exact + FTS5 ConceptIndex
  -> Concept seed
  -> seed one-hop
  -> seed -> current user anchor shortest path
  -> merge union into Working Graph
```

The Working Graph is temporary per-turn state and is not persisted as another graph.

## 8. Deliberate memory expansion

`memory_search(node_id)` expands exactly one permanent-graph hop and merges that neighborhood into the current Working Graph. Newly visible nodes also receive available shortest paths back to the current user's anchor.

There is no arbitrary-depth hidden traversal; farther recall requires another explicit `memory_search` call.

## 9. Post-response semantic update

No interpreted graph memory is written during the native tool-use loop.

```text
raw user evidence saved
  -> preflight
  -> auto-recall
  -> agent/tool loop
  -> final answer accepted
  -> MemoryRuntime.finish_turn()
       create Utterance Node
       connect user_anchor -> utterance (spoke)
       create/reuse Sentence_Breaker Concepts
       connect utterance -> concept (mentions)
       extract user-grounded Facts
       connect anchor/utterance/fact provenance
       index only newly-created Concept Nodes
```

Tool/search-derived world facts require their own evidence-bearing source policy and must not be silently attributed to the user.

## 10. Failure semantics

Memory follows the same MAI rules as the agent runtime:

- no string-contains routing;
- no similarity-based identity merging;
- no silent replacement of failed required tools;
- no rewriting source utterances;
- no free-form model edge meaning used as evidence;
- SQLite/FTS5/index contract failures remain explicit;
- conflicting Concept index identity fails visibly;
- a required `memory_search` that did not successfully run cannot be considered satisfied.

## 11. Implementation boundaries

```text
mai/memory/
  graph/
    schema.py       typed permanent graph schema
    models.py       anchor/utterance/fact/concept/edge values
    repository.py   graph identity, typed edges, one-hop and anchor paths
  index/
    base.py         ConceptIndex / ConceptHit protocol values
    sqlite_fts.py   exact hash + SQLite FTS5 backend
  working.py        per-turn Working Graph
  segmenter.py      Sentence_Breaker adapter
  recall/           concept entry + one-hop + user-anchor path assembly
  extraction/       post-response user FactExtractor contract
  runtime.py        memory lifecycle coordinator
  tools.py          user-bound native memory_search
```

The design goal is:

> **MK4-style evidence/provenance graph memory with a model-independent exact + lexical ConceptIndex.**
