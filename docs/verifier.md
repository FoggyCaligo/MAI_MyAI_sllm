# Final answer verification

This branch restores the original integrated review policy from the current main
commit, but divides its responsibility into two ordered reviewer calls.

1. The historical **Numeric Reviewer** is retained, including deterministic numeric/date checks and optional semantic equivalence review.
2. The **Factual Reviewer** checks candidate claims against observed user/tool evidence, source scope, timestamps, and claimed external action outcomes. This reviewer receives the full evidence payload. It does not judge completeness or task alignment.
3. Only after factual grounding and action-outcome checks pass does the **Answer Quality Reviewer** assess evidence coverage, alignment with the user's request, and truthful partial-answer policy. It receives the candidate, conversational request/context, and factual findings, not the entire tool-result payload.

Both stages review the *already-created provisional final answer*. After both pass, the provisional answer is returned unchanged. On rejection the existing agent loop returns control to a model round with tool access and correction feedback. The next candidate is rechecked from the beginning.

## Configuration

`MAI_ANSWER_QUALITY_REVIEW=true` (default): run Answer Quality after Factual passes.

`MAI_ANSWER_QUALITY_REVIEW=false`: skip Answer Quality and release after Factual passes, assuming Numeric also passes.

The setting accepts the exact values `true` and `false` (case insensitive). Invalid values fail at verifier initialization.

This toggle does not disable numeric or factual grounding checks.

## Scope and limitations

The factual grounding and quality sections preserve the original integrated prompt's policies as closely as possible, with separate structured response schemas. Action-outcome evidence validation stays with factual review; user-request alignment stays with quality review. The existing per-axis retry budgets remain the same.

Quality review no longer sees raw tool logs. It can judge whether the draft addresses the request, but assessing **omitted facts present only in tool results** may be less reliable than with the original integrated reviewer. This is a deliberate speed/coverage tradeoff to measure, not a guarantee of equal accuracy. Two full LLM stages can still be slower than the integrated reviewer, although Quality receives a smaller input.

Older tests expecting exactly one integrated reviewer call require migration to the two-stage contract. No claim of end-to-end Ollama test success is made.
