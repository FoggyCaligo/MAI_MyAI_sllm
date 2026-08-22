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

Graph recall에서 provenance를 통해 필요한 원문을 다시 조회할 수 있어야 한다.

Raw source와 graph의 ownership은 동일 user boundary를 따라야 한다. Source가 삭제/유실된 경우 이를 다른 문자열로 대체하거나 추측하지 않고 missing source로 명확히 드러낸다.

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

## Phase 5 — Remaining MK4 parity checks

다음은 위 핵심 작업 이후 재평가한다.

- model-friendly tool-name adapter
- richer account/session controls (TTL, maximum active sessions)
- voice STT/TTS

MK4의 global round cap, hidden fallback synthesis, parse-success fallback처럼 현재 Mai의 fail-visible contract와 충돌하는 기능은 parity 대상으로 간주하지 않는다.
