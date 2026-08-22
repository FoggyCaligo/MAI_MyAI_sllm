# Mai MK4 Parity Roadmap

Mai가 MK4의 실사용 안정성과 문맥 유지 능력을 넘기 전까지, 새로운 문제를 해결할 때 먼저 MK4의 동종 구현을 확인한다. MK4 구현이 현재 Mai의 계약과 충돌하지 않으면 그 구조를 우선 참고한다.

## Memory/source model

세 종류의 memory를 분리한다.

```text
raw conversation history = 최근 대화 continuity
semantic graph           = 장기 의미 기억
scratchpad               = 현재 작업 중 임시 working memory
```

Raw conversation을 자동으로 semantic graph로 복제하지 않는다.

### Graph source provenance
Graph node/edge는 의미만 저장하되, 그 의미가 만들어진 원문 source로 구조적으로 역추적 가능해야 한다. stable source reference를 사용하며 source가 유실되면 추측하지 않고 missing source로 드러낸다.

### Graph confidence and lazy source disclosure
기본 recall은 `semantic relation + confidence + source_kind`로 compact하게 유지한다. 상세 provenance summary와 raw source는 모델이 필요할 때만 lazy inspection으로 연다. `confidence`는 source reliability, support/conflict, stability, source availability/freshness처럼 Framework가 구조적으로 알고 있는 신호를 압축한 값이며 문장 의미 휴리스틱으로 계산하지 않는다. Assistant-origin evidence는 `assistant가 말했다`는 사실과 그 내용의 world-fact certainty를 분리한다.

## Phase 1 — Model context parity
- 최근 대화 context
- tool result compaction
- recent tool-operation context
- current date system injection

## Phase 2 — Agent stability parity
- 동일 successful action 반복 계약 (#31 재작성)
- Autonomy retry
- web evidence grounding pass

## Phase 3 — Session, authorization, and working context
- owner/trial별 tool 제한
- persistent authenticated session
- request-detached chat execution
- session별 file working context/root

## Phase 4 — Attachment and working memory
- attachment automatic read/analyze
- model-managed scratchpad
- file/tool evidence → scratchpad carryover
- final graph mutation에서 scratchpad 참고
- scratchpad source provenance

## Phase 5 — Graph provenance, confidence, and source inspection
- graph node/edge → stable raw source reference
- compact default recall: relation + confidence + source_kind
- provenance-summary lazy inspection
- raw-source lazy inspection
- user/assistant/web/file/scratchpad source distinctions
- support/conflict/stability structural metadata

## Phase 6 — Remaining MK4 parity checks
- model-friendly tool-name adapter
- richer account/session controls
- voice STT/TTS

MK4의 global round cap, hidden fallback synthesis, parse-success fallback처럼 현재 Mai의 fail-visible contract와 충돌하는 기능은 parity 대상으로 간주하지 않는다.
