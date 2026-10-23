# Verifier architecture

The historical numeric/date review is retained from commit `be19c7d7`.
When a provisional answer is produced, the verifier runs these stages:

1. **Numeric Reviewer** — deterministic numeric/date grounding and the original optional numeric LLM review for values requiring semantic equivalence.
2. **Coverage and evidence preparation LLM** — checks essential requested outcomes. A missing optional memory item in an open-ended summary is not grounds for rejection. On `insufficient` coverage it returns a short correction reason and no claim list; the release LLM is skipped. Otherwise it identifies factual claims and selects existing `evidence_refs`.
3. **Grounding and Alignment release LLM** — receives the candidate, request and prepared claim/evidence JSON. It decides whether each referenced value supports its claim, whether the answer fulfills the user's request, and whether any claimed action has a verified outcome.

Python registers each *leaf value* of successful JSON tool outputs as a reference such as `e0` with its tool name, original result index and JSON path. Non-JSON strings are registered whole. User messages are also registered as evidence entries. Failed tool results are excluded. The second LLM chooses reference IDs instead of constructing excerpts or calculating character positions. Python resolves chosen IDs to original source values before calling stage three; nonexistent IDs are marked `invalid_reference` and absent selections `unlocated`. A valid reference is not automatically a supporting reference; stage three decides semantic support.

A candidate rejected for coverage, numeric grounding, unsupported claims, alignment, or action outcome is **not published**. The existing `AgentLoop` passes a correction contract and rejected draft into the following model round, where the agent can call tools again. The same existing per-axis correction budgets apply. Once verified, the provisional text is returned unchanged. Memory storage remains a separate pipeline.

Limitations: a long plain-text tool result remains a long single evidence entry; this prioritizes correctness over aggressive text segmentation. The extra semantic release call can also add latency. Real Ollama integration/performance tests are required after merging.
