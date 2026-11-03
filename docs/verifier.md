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
`location`, and a short verbatim `quote`. References are optional supporting
metadata for every defect, including `contradiction`, `scope_expansion`, and
`unsupported_inference`. Absence of support must not be disguised as an observed
opposing fact or a replacement count. The reviewer is asked to identify rejected
claims and give concrete reasons; an overall rejection can still use the existing
semantic-feedback fallback when claim-level findings are unavailable.

Sources use run-local IDs: `tool:<original execution index>` for successful tool
results, `user:<user_evidence index>`, `prior:<verified_prior_conversation index>`,
and `time` for the authoritative runtime clock. Failed calls, rejected drafts,
and verifier diagnoses are not citation sources. Index gaps from failed calls
are preserved. JSON tool outputs may be quoted as serialized text or from their
decoded string fields, with whitespace normalization only.

The runtime checks source existence and quote provenance on a best-effort basis.
This does **not** prove that the reviewer's interpretation, record location, or
calculation is correct. Omitted, null, or malformed reference metadata becomes an
empty list; invalid individual references are dropped while valid ones survive.
Warnings are logged, and provenance warnings are recorded in review details.
Reference problems do not cause reviewer retries, HTTP errors, or an automatic
change to the factual verdict. The ordinary factual correction loop continues,
and supported answers can be delivered even when reference metadata is invalid.
Core verdict/schema and provider failures retain their existing handling.

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
