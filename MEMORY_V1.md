# MAI Memory v1

This document defines MAI's long-term memory schema and runtime contract without relying on any earlier MACHI/MK repository. The memory system is an evidence-preserving graph built from user anchors, original utterances, optional user-grounded facts, reusable concepts, typed edges, and provenance. Model-visible recall enters that graph through explicit native memory tools backed by a model-independent ConceptIndex.

## 1. Core idea

Sentence_Breaker defines reusable **Concept Node boundaries**, not the entire memory graph.

```text
User utterance
  -> immutable raw Evidence
  -> after final response:
       Utterance Node (original sentence)
       Concept Nodes (Sentence_Breaker segments)
       optional Fact Nodes (when a FactExtractor is configured)
```

Concept identity is exact segment identity:

```text
one canonical Sentence_Breaker segment = one Concept Node
```

Repeated appearances of the same segment reuse the same Concept Node. Recall indexing never merges graph identity.

The full original sentence remains a first-class Utterance Node so recall can show the model what the user actually said instead of forcing it to trust a rewritten relation description.

The current production runtime does **not** configure a model-backed `FactExtractor`. Therefore raw Utterance and Concept memory are active today, while Fact nodes remain part of the schema/extension contract rather than something every production turn currently creates.

## 2. Permanent graph node types

### User Anchor

Every memory identity owns one persistent anchor:

```text
user_anchor::<memory_user_id>
```

The anchor is a permanent graph node but is never inserted into the ConceptIndex. It establishes whose memory a recalled subgraph belongs to.

### Utterance Node

An Utterance Node contains the original user sentence and an immutable `evidence_id`.

```text
User Anchor
    └─spoke→ Utterance
```

### Fact Node

A Fact Node is a concise long-term fact derived from a user's utterance. It never replaces its source utterance.

```text
User Anchor ─asserted_fact→ Fact
Utterance   ─derived_fact─→ Fact
```

Fact is a supported permanent node type, but current production runs with `fact_extractor=None`, so Fact creation is not part of the active default write path.

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

Relations involving Fact nodes are used only when Fact extraction is actually enabled.

## 4. Evidence

Raw user input is stored in the immutable `evidence` table before the agent run. Recording raw evidence is not semantic graph mutation. After the final answer, an Utterance Node is created for that evidence and connected to the user's memory anchor and its Concept nodes.

This lets the system answer not only "what is remembered?" but also "what did the user actually say that produced this memory?"

## 5. Production runtime ordering

The current production request path is **pure-agent C**. It has no Tool Requirement Preflight and no automatic recall stage.

```text
User input
  -> record immutable raw evidence
  -> create per-turn Working Graph
  -> expose role-appropriate native tools
  -> main Ollama-native agent loop
       memory tools are available like other native capabilities
       the model chooses whether and when to call them
  -> final response accepted
  -> post-response memory update
```

Memory is therefore not injected into every turn automatically. If user history is needed, the model explicitly calls a model-visible memory tool.

Preflight/required-tool classes may still exist elsewhere in the source tree for older experiments or lower-level compatibility, but they are not part of the production MAI request lifecycle.

## 6. Model-independent ConceptIndex: Exact + SQLite FTS5

Memory v1 does not require embeddings or a vector database. Persistent recall entry is handled by a `ConceptIndex` boundary whose concrete implementation is `SqliteFtsConceptIndex`.

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

```text
Memory Runtime
    -> ConceptIndex
         -> SqliteFtsConceptIndex
```

There is no embedding model configuration and no model-specific vector space. Changing the main LLM or a future memory-writing LLM therefore does not require rebuilding long-term memory or recall coordinates.

When `SqliteFtsConceptIndex` opens an existing Memory v1 database, it non-destructively synchronizes permanent Concept Nodes that are not yet present in the exact/FTS tables. Legacy sqlite-vec tables, if present from an older development database, are ignored rather than silently deleted.

## 7. Model-visible memory recall

The ConceptIndex is the graph entry point used by explicit memory tools. A Concept hit is not itself a final memory answer.

Current model-visible memory entry points are:

```text
memory_overview(limit)
  -> broad memory view without a lexical query

memory_recall(query)
  -> Sentence_Breaker query segments
  -> Exact + FTS5 ConceptIndex
  -> Concept seeds
  -> graph neighborhoods / available user-anchor paths
  -> merge into the per-turn Working Graph

memory_search(node_id)
  -> exact one-hop expansion from a selected permanent node
  -> merge into the per-turn Working Graph
```

Shortest-path discovery treats topology as undirected, while returned edges preserve stored direction, relation, and provenance.

The Working Graph is temporary per-turn state and is not persisted as another graph. It is populated only by explicit memory-tool execution in the production C runtime; there is no automatic recall pass before the main agent loop.

## 8. Deliberate memory expansion

`memory_search(node_id)` expands exactly one permanent-graph hop and merges that neighborhood into the current Working Graph. Newly visible nodes may also receive available shortest paths back to the current user's memory anchor.

There is no arbitrary-depth hidden traversal; farther recall requires another explicit memory call.

## 9. Post-response memory update

No interpreted graph memory is written during the native tool-use loop.

```text
raw user evidence saved
  -> agent/tool loop
  -> final answer accepted
  -> MemoryRuntime.finish_turn()
       create Utterance Node
       connect user_anchor -> utterance (spoke)
       create/reuse Sentence_Breaker Concepts
       connect utterance -> concept (mentions)
       if FactExtractor is configured:
         create user-grounded Facts
         connect anchor/utterance/fact provenance
       index only newly-created Concept Nodes
```

Current production uses `fact_extractor=None`; therefore the active default write path preserves raw Utterance evidence and Concept links without silently pretending semantic Fact extraction succeeded.

Tool/search-derived world facts have a different source from user assertions and must not be silently stored as if the user had said them.

## 10. Correction and conflict

Long-term memory is revisable. A later correction does not justify deleting the original evidence and pretending it never existed.

The design principle is to preserve source evidence and represent newer interpretations through explicit provenance/version or conflict relations rather than destructive rewriting.

User-model correction, factual/content correction, and response-style correction remain distinct meanings and must not all be collapsed into profile mutation.

This is a memory-design contract. A complete production correction-application pipeline is not assumed to be implemented merely because the graph schema can represent it.

## 11. Failure semantics

Memory follows the same MAI rules as the agent runtime:

- no string-contains routing;
- no similarity-based identity merging;
- no silent replacement of failed memory calls;
- no rewriting source utterances;
- no free-form model edge meaning used as evidence;
- no claiming Fact extraction occurred when `FactExtractor` is absent;
- SQLite/FTS5/index contract failures remain explicit;
- conflicting Concept index identity fails visibly;
- a failed memory tool result must not be treated as successful recall.

## 12. Implementation boundaries

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
  extraction/       optional post-response user FactExtractor contract
  runtime.py        memory lifecycle coordinator
  tools.py          user-bound native memory tools
```

The design goal is:

> **Evidence-preserving graph memory with explicit model-driven recall and a model-independent exact + lexical ConceptIndex.**
