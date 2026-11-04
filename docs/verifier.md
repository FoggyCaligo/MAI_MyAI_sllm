# Final answer verification

Verification has a deterministic numeric precheck, one combined factual/numeric
model review, and an optional answer-quality review.

1. Python checks numeric/date values against original user/tool evidence. A failed token match identifies values needing semantic equivalence review; a Python exception remains an error.
2. The **Factual Reviewer** checks claims, source scope, timestamps and claimed external action outcomes. When Python flags values, the same request includes the numeric-equivalence instructions and flagged values, and the structured response requires `numeric_verdict` and `numeric_reason`. There is no separate numeric-model request. The reviewer initially receives retained verbatim excerpts and source references, and can request original ranges as described below. It does not judge completeness or task alignment.
3. Only after factual grounding and action-outcome checks pass does the **Answer Quality Reviewer** assess evidence coverage, alignment with the user's request, and truthful partial-answer policy. It receives the candidate, conversational request/context, and factual findings, not the entire tool-result payload.

Both stages review the *already-created provisional final answer*. After both pass, the provisional answer is returned unchanged. On rejection the existing agent loop returns control to a model round with tool access and correction feedback. The next candidate is rechecked from the beginning.

## Configuration

`MAI_ANSWER_QUALITY_REVIEW=true` (default): run Answer Quality after Factual passes.

`MAI_ANSWER_QUALITY_REVIEW=false`: skip Answer Quality and release after Factual passes, assuming Numeric also passes.

The setting accepts the exact values `true` and `false` (case insensitive). Invalid values fail at verifier initialization.

This toggle does not disable numeric or factual grounding checks. A missing
combined numeric verdict is a schema violation, not numeric success. Quality is
skipped when numeric or factual grounding fails.

## Retained evidence and original reads

Each ordinary tool result is stored immutably with a run-local `result_id` and
SHA-256. The main model first receives a bounded page. After consuming it, that
page is replaced by selected verbatim excerpts plus the original reference.
The main model uses the existing `tool_manual.retain_evidence` argument to select
`{result_id, ranges: [{offset, length}]}`. Offsets count zero-based Python Unicode
code points. Selection replaces ranges for that result; empty ranges clear them.
Selection can accompany other native calls in the same main-model round. There
is no separate summarization model. Without a selection, only the source
reference survives; this is explicitly partial evidence, not an empty original.

Ranges are extracted by the runtime, never rewritten by a helper. Unknown IDs,
overlapping/out-of-bounds ranges or selections over the result budget fail
explicitly without partial activation or selection changes. Originals stay
available through `tool_result_read`. Pagination reads keep the original ID and
are not recaptured as new observations. Activation/selection acknowledgments and
pagination reads are excluded from factual evidence; original execution indices
are preserved for actual observations.

The factual reviewer still has no native execution tools. When stored sources
are present, its structured response is either:

```json
{"read_requests": [{"source_id": "tool:1", "offset": 100, "limit": 500}], "review": null}
```

or `{"read_requests": [], "review": { ... complete factual/numeric verdict ... }}`.
The runtime serves only successful sources from this run, within the existing
read budget, and puts exact original text in that source's `reread_excerpts`.
It then asks the reviewer to continue. Original reads may add model calls; they
are not new observations and never rerun an external tool or action. Unknown or
failed sources and invalid ranges are errors. Duplicate/contained/overlapping
ranges in a request batch are deduplicated; only previously unprovided original
characters are appended. A wholly cached read round returns an explicit
`original_read_status` with `already_provided` results so the reviewer can use
existing text and finish. Two consecutive rounds without new characters exhaust
the progress budget and raise an error; new original characters reset it.
This never changes an unsupported verdict into supported. Source quotes may be
validated against retained or reread original text. Fatal reviewer errors preserve
the last draft only as explicitly unverified diagnostic output, alongside the
original error; they do not release it as a verified final answer.

Numeric token matching scans original text in Python, not compaction metadata.
The LLM still receives only retained/reopened evidence. This avoids treating
metadata lengths or offsets as observed factual quantities.

Both synchronous and detached web-chat audit logs preserve complete original
tool results and `result_id` in the existing `tool_log` steps. UI/model result
budgets do not truncate these audit originals. Audit storage does not provide
cross-run retrieval; original reads remain scoped to the running agent.

## Scope and limitations

Action-outcome evidence validation stays with factual review; user-request
alignment stays with quality review. Existing per-axis correction budgets and
provider/schema retry budgets remain unchanged. Valid supported/unsupported
verdicts are not rerolled. Original-read failures propagate as errors.

Quality review no longer sees raw tool logs. It can judge whether the draft addresses the request, but assessing **omitted facts present only in tool results** may be less reliable than with the original integrated reviewer. This is a deliberate speed/coverage tradeoff to measure, not a guarantee of equal accuracy. Two full LLM stages can still be slower than the integrated reviewer, although Quality receives a smaller input.

Neither unit tests nor fewer input characters guarantee end-to-end Ollama
latency or accuracy gains. Retention quality depends on the main model;
independent original reads are available to check omitted qualifiers or contrary
evidence and may offset savings if many ranges need reopening.

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
