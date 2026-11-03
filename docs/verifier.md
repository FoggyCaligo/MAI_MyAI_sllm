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

## Traceable factual rejections and correction

Factual claim findings can include `evidence_refs`, each with `source_id`,
`location`, and a short verbatim `quote`. A rejection for `contradiction`,
`scope_expansion`, or `unsupported_inference` requires at least one reference.
`missing_evidence` may have an empty reference list: absence of support must not
be disguised as an observed opposing fact or a replacement count. An overall
factual rejection must identify an unsupported claim rather than provide only
an unlocated diagnosis.

Sources use run-local IDs: `tool:<original execution index>` for successful tool
results, `user:<user_evidence index>`, `prior:<verified_prior_conversation index>`,
and `time` for the authoritative runtime clock. Failed calls, rejected drafts,
and verifier diagnoses are not citation sources. Index gaps from failed calls
are preserved. JSON tool outputs may be quoted as serialized text or from their
decoded string fields, with whitespace normalization only.

The runtime checks source existence and quote provenance before accepting the
review. This does **not** prove that the reviewer's interpretation, record
location, or calculation is correct. Invalid references use the existing bounded
reviewer-contract retry path (at most three calls), with a repair instruction;
they do not become correction facts for the main model. Exhaustion remains an
explicit reviewer error and does not release the candidate as verified.

Validated references are preserved in review details, audit rejection records,
the correction contract, and the latest correction feedback. Two grounding
rejections against unchanged successful evidence add an instruction to
re-observe, change the calculation/verification approach, or narrow/remove the
claim. Repeating an identical successful result does not count as new evidence.
This is a correction instruction, not a hard requirement for another tool call:
a properly narrowed supported answer remains possible. Existing retry limits
and latest-feedback-only behavior are retained.

## Identity assumptions and interpretations

The main runtime asks the model to establish ambiguous referents with user
context or a neutral lookup before adding guessed categories to queries. It
instructs the model to revisit assumptions when observed titles, snippets, or
page text conflict with them. Empty/irrelevant search results do not confirm a
guess. The factual reviewer receives matching identity checks even when the
optional quality review is off.

Both prompts distinguish a qualified, evidence-based metaphor or interpretation
requested by the user from a factual assertion about documented creator intent.
The factual premises still require evidence. These prompt changes need local
model evaluation; unit tests do not establish an accuracy or latency gain.
