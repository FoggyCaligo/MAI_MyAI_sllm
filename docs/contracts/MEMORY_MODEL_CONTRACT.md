# Memory Model Contract — Superseded

## Status

This document is retained only to make the architectural transition explicit.
The dedicated post-answer memory-model design is no longer the target runtime contract.

The canonical memory architecture is now:

- [`AGENT_GRAPH_MEMORY_CONTRACT.md`](AGENT_GRAPH_MEMORY_CONTRACT.md)

## Superseded design

The following architecture is retired:

```text
Agent loop
  -> fixed answer
  -> dedicated Qwen memory loop
  -> GraphCommitPhase
  -> answer release
```

The runtime must not require a second top-level memory model or `MAI_OLLAMA_MEMORY_MODEL` for normal graph memory operation.

The following responsibilities are removed from the target architecture:

- dedicated Qwen memory-model orchestration;
- post-answer `GraphCommitPhase` as a separate model loop;
- `continue_memory` protocol;
- answer freeze followed by mandatory graph commit;
- scratchpad selection as an intermediary between the Agent and graph memory.

## Current model contract

There is one explicit Agent loop.
Each Agent round maps to exactly one structured LLM request.
The same Agent may, between rounds:

- run vector memory recall;
- open selected nodes and one-hop neighborhoods;
- update the turn-scoped ViewedGraph;
- generate/fix persistent graph nodes and edges;
- use file/web/other tools;
- return the final answer after graph-sync confirmation.

Graph mutations commit immediately to SQLite and are visible to later Agent rounds and future turns.

There is no hidden review/retry model call inside the model adapter.
If another model decision is needed, it is the next explicit Agent round.

## Embedding configuration

Semantic candidate recall uses a separate embedding model configured through `.env`:

```env
MAI_OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```

The embedding model is not a second reasoning/memory LLM. It only produces vectors for node/query similarity search.

Embedding failure must fail visibly. The framework must not silently fall back to lexical memory search.

## Rationale

The dedicated Qwen memory loop was introduced because it produced better graph extraction than the conversational model. The architecture has since changed: the graph itself is now the Agent's live working memory, so delaying all graph mutation until after the answer creates an unnecessary second reasoning phase and prevents the Agent from seeing its own graph changes while it works.

The new contract therefore accepts that graph quality is the responsibility of the single Agent model and prioritizes a simpler, continuously updated graph workflow.
