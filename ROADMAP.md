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

Graph node/edge는 의미만 저장하되, 그 의미가 만들어진 원문 source로 구조적으로 역추적 가능해야 한다.

현재 `graph_provenance.source_text`처럼 원문 문자열 사본만 저장하는 방식에서 발전시켜, provenance가 stable source reference를 갖도록 한다.

예정 source kinds:

- `chat_message`: raw user/assistant message
- `tool_operation`: file/terminal/code/document/image tool evidence
- `web_evidence`: web search/research evidence
- `scratchpad`: final graph mutation에 실제 근거로 사용된 working-memory item

Raw source와 graph의 ownership은 동일 user boundary를 따라야 한다. Source가 삭제/유실된 경우 이를 다른 문자열로 대체하거나 추측하지 않고 missing source로 명확히 드러낸다.

### Graph confidence and lazy source disclosure

MK4의 provenance/trust/stability 개념을 Mai에도 통합하되, 상세 provenance를 모든 recall에 전부 넣어 context를 비대하게 만들지 않는다.

기본 recall은 가능한 한 compact하게 유지한다.

```text
Level 1 — default recall
semantic relation + confidence + source_kind

Level 2 — provenance summary
source reliability + support/conflict + stability + compact source metadata

Level 3 — raw source
실제 chat message / tool evidence / web evidence / file evidence
```

Level 2와 Level 3은 모델이 필요할 때만 구조화된 source/provenance inspection을 통해 lazy하게 조회한다. Tool의 `tool_manual`과 같은 lazy-disclosure 원칙을 memory에도 적용한다.

`confidence`는 모델이 임의로 정하는 문장 의미 점수가 아니라 Framework가 이미 알고 있는 구조적 신호를 compact하게 표현한 값이다. 후보 신호는 다음과 같다.

- source kind/reliability
- support count
- conflict signal/count
- stability
- source availability/freshness처럼 구조적으로 확인 가능한 metadata

세부 계산식은 구현 전에 MK4의 trust/stability 사용 방식을 기준으로 검토한다. Framework가 문장 내용을 문자열/의미 휴리스틱으로 읽어서 confidence를 결정하지 않는다.

`source_kind`는 compact recall에도 유지한다. 같은 confidence라도 user statement, assistant statement, web evidence는 성격이 다르기 때문이다.

Assistant statement의 경우 `assistant가 그렇게 말했다`는 provenance 자체와 `그 내용이 외부 사실이다`는 판단을 분리한다. MK4처럼 assistant-origin evidence가 곧 world-fact certainty가 되지 않도록 한다.

## Phase 1 — Model context parity

필수:

- 최근 대화 context
- tool result compaction
- recent tool-operation context
- current date system injection

규칙:

- 최근 대화는 raw text로 저장하고 최근 message만 model context에 주입한다.
- graph는 final memory mutation에서 모델이 선택한 semantic relation만 저장한다.
- tool result 원본은 runtime event에 보존하고 model-facing copy만 compact한다.

## Phase 2 — Agent stability parity

필수:

- 동일 successful action 반복 계약 (#31 재작성)
- Autonomy retry
- web evidence grounding pass

동일 action guard는 tool/error 문자열 휴리스틱이 아니라 structured tool name + canonical arguments identity를 사용한다.

Autonomy retry는 exposed capability가 있는데 실제 execution failure 없이 모델이 unsupported/blocked로 끝내는 경우에만 구조적으로 한 번 재검토시키는 MK4 패턴을 참고한다.

Web grounding은 실제 web evidence ID/reference와 final factual answer의 근거 연결을 검증한다. 근거 부족을 임의 지식으로 보충하지 않는다.

## Phase 3 — Session, authorization, and working context

필수:

- owner/trial별 tool 제한
- persistent authenticated session
- 다른 앱/탭을 보고 돌아와도 chat/job 상태가 끊기지 않는 request-detached execution
- session별 file working context/root

권한은 Framework가 구조적으로 강제한다. 모델이 user text를 보고 owner/trial을 추론하지 않는다.

Session별 working directory/root는 file tool의 discovery root와 합쳐 하나의 working-context abstraction으로 관리한다.

## Phase 4 — Attachment and working memory

필수/권장:

- attachment automatic read/analyze
- model-managed scratchpad
- file/tool evidence → scratchpad working-memory carryover
- final graph mutation에서 scratchpad 참고 가능
- scratchpad source provenance

첨부파일 자동 처리는 파일 종류라는 구조적 정보로 reader/analyzer capability를 결정하며 사용자 문장의 의미를 문자열 휴리스틱으로 route하지 않는다.

Scratchpad는 현재 작업용 temporary state다. 모델이 tool evidence에서 중요한 내용을 scratchpad에 기록/갱신할 수 있어야 하며, final answer/memory plan을 만들 때 읽을 수 있어야 한다.

Scratchpad 전체를 자동으로 durable graph에 저장하지 않는다. Final graph mutation에서 모델이 선택한 semantic facts만 graph에 들어간다. Graph provenance는 그 mutation이 참조한 scratchpad/source까지 역추적 가능해야 한다.

## Phase 5 — Graph provenance, confidence, and source inspection

필수:

- graph node/edge → stable raw source reference
- compact default recall: relation + confidence + source_kind
- provenance-summary lazy inspection
- raw-source lazy inspection
- user/assistant/web/file/scratchpad source distinctions
- support/conflict/stability structural metadata

Graph recall이 원문 전체를 기본 payload에 포함하지 않도록 한다. 상세 evidence는 필요할 때만 연다.

Graph source reference가 가리키는 raw source는 삭제되거나 유실될 수 있다. 이 경우 source inspection은 missing source failure를 명확히 반환하며 다른 텍스트를 대신 사용하지 않는다.

## Phase 6 — Remaining MK4 parity checks

다음은 위 핵심 작업 이후 재평가한다.

- model-friendly tool-name adapter
- richer account/session controls (TTL, maximum active sessions)
- voice STT/TTS

MK4의 global round cap, hidden fallback synthesis, parse-success fallback처럼 현재 Mai의 fail-visible contract와 충돌하는 기능은 parity 대상으로 간주하지 않는다.
