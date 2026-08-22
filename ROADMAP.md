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

동일 사용자 graph에서 모델이 `new_node`를 제안하더라도 trim 이후 정확히 같은 node name이 이미 존재하면 새 node를 만들지 않고 기존 node를 재사용한다. 동일 `(subject, relation, object)` edge는 새 edge를 복제하지 않고 `support_count`를 강화한다. 비슷한 의미의 서로 다른 문자열을 Framework가 휴리스틱으로 합치지는 않는다.

### Graph source provenance

Graph node/edge는 의미를 저장하고, 장기기억 mutation의 근거로 실제 채택된 source는 `graph.sqlite3`의 durable source store에 별도로 저장한다.

Source kinds:

- `user_message`
- `assistant_message`
- `web_evidence`
- `file_evidence`
- `tool_operation`
- `scratchpad`

Graph target과 source는 stable source ID로 연결하며 동일 user ownership boundary를 따른다.

### Graph confidence and lazy source disclosure

```text
Level 1 — default recall
semantic relation + confidence + source_kind

Level 2 — memory_source_summary
source reliability + support/conflict + stability + compact source metadata

Level 3 — memory_source_read
실제 raw evidence를 bounded excerpt로 조회
```

`confidence`는 모델이 임의로 만드는 의미 점수가 아니라 Framework가 구조적으로 알고 있는 source reliability, support count, revision/conflict count, stability를 압축한 값이다. Framework가 문장 의미를 문자열 heuristic으로 읽어 confidence를 결정하지 않는다.

## Phase 1 — Model context parity

구현됨:

- 최근 대화 context
- tool result compaction
- recent tool-operation context
- current date system injection

최근 대화는 raw text로 저장하고 최근 message만 model context에 주입한다. Graph는 final memory mutation에서 모델이 선택한 semantic relation만 저장한다. Tool result 원본은 runtime event에 보존하고 model-facing copy만 compact한다.

## Phase 2 — Agent stability parity

구현됨:

- 동일 successful action 반복 계약
- Autonomy retry
- web evidence grounding pass

동일 action guard는 structured tool name + canonical arguments identity를 사용한다. Autonomy retry는 구조적 `blocked` outcome에만 적용한다. Web grounding은 실제 evidence ID와 proposed final answer의 근거 연결을 검증하고 답변 문장 자체를 재작성하지 않는다.

## Phase 3 — Session, authorization, and working context

구현됨:

- owner/trial별 tool 제한
- persistent authenticated session
- request-detached chat execution
- session별 file/code working context/root
- trial user ID당 active session 1개

Trial은 host filesystem/terminal/code/document/image work tool을 catalog에서부터 받지 않는다. 다만 자기 account upload directory의 첨부파일은 upload하고 automatic attachment evidence로 읽거나 분석할 수 있다.

Session token 원문은 DB에 저장하지 않고 hash만 저장한다. Queued job은 실행 직전 stable session ID를 다시 검증한다.

## Phase 4 — Attachment and working memory

구현됨:

- attachment automatic read/analyze
- attachment evidence의 model-only context 주입
- normal work-tool result에 turn-local evidence ID 부여
- model-managed `scratchpad_put` / `scratchpad_update`
- attachment/tool evidence -> scratchpad carryover
- final memory mutation의 optional `scratchpad_ids`
- current-turn evidence/scratchpad scope validation
- turn 종료 시 evidence/scratchpad registry 제거

Scratchpad 전체를 durable graph에 자동 저장하지 않는다. Final graph mutation에서 모델이 명시적으로 선택한 scratchpad와 그 underlying evidence만 장기 source provenance 후보가 된다.

자세한 계약은 `docs/contracts/WORKING_MEMORY_CONTRACT.md`를 참조한다.

## Phase 5 — Graph provenance, confidence, and source inspection

구현됨:

- `graph_sources` durable source store
- `graph_source_links`를 통한 graph node/edge → stable raw source reference
- source kinds: user / assistant / web / file / tool / scratchpad
- 동일 source identity collision의 명시적 실패
- default recall edge에 compact `confidence + source_kind`
- `support_count` 기반 reinforcement
- `revise_memory` 기반 conflict signal
- source-kind reliability/stability 기반 구조적 confidence
- `memory_source_summary` lazy provenance inspection
- `memory_source_read` bounded raw-source inspection
- scratchpad를 채택한 memory mutation에서 실제 underlying attachment/tool/web evidence까지 durable source로 연결

Graph recall은 원문 전체를 기본 payload에 포함하지 않는다. Summary에서 실제로 확인된 source ID만 raw read scope에 들어간다.

자세한 계약은 `docs/contracts/GRAPH_SOURCE_CONTRACT.md`를 참조한다.

## Phase 6 — Remaining MK4 parity checks

다음 항목은 핵심 memory/runtime parity 이후 재평가한다.

- model-friendly tool-name adapter
- maximum active sessions / explicit session revocation controls
- voice STT/TTS

현재 trial account는 이미 user ID당 active session 1개를 강제하므로, Phase 6의 session 항목은 owner/admin용 명시적 session 관리 UI/API가 실제로 필요한지 재평가한다.

MK4의 global round cap, hidden fallback synthesis, parse-success fallback처럼 현재 Mai의 fail-visible contract와 충돌하는 기능은 parity 대상으로 간주하지 않는다.
