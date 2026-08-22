# Mai Attachment / Scratchpad Working-Memory Contract

이 문서는 Phase 4의 attachment evidence와 turn-local scratchpad 계약을 정의한다.

## 1. Attachment routing is structural

첨부 자동 처리는 사용자 문장 의미를 문자열 규칙으로 해석하지 않는다.

Framework는 실제 uploaded file의 suffix/type만 보고 reader를 선택한다.

- known text/code suffix -> strict UTF-8 text read
- `.pdf` -> PDF text extraction
- `.docx` -> paragraph extraction
- known image suffix -> independent vision model
- unsupported suffix -> `unsupported_attachment_type`

Decode/parse/image verification/model failure는 다른 reader로 자동 fallback하지 않고 실제 실패로 드러낸다.

## 2. Model-only attachment evidence

Attachment raw evidence를 user text 문자열에 합치지 않는다.

자동 추출 결과는 `ModelContext.attachment_evidence`로만 주입한다.

따라서:

- raw chat history에는 원래 user message만 저장된다.
- `MemoryTurnScope.user_text`도 원래 user message를 유지한다.
- 첨부 원문 전체가 모든 graph mutation provenance에 자동 포함되지 않는다.

Attachment evidence는 turn-local ID를 가진다.

```text
attachment:1
attachment:2
...
```

Context 비대화를 막기 위해 automatic extraction은 명시적인 character budget을 가진다. Budget으로 읽지 못한 첨부는 성공으로 추측하지 않고 `not_loaded_context_budget` 상태를 모델에 보여준다. 원본 path는 current-turn path provenance에 남으므로 owner agent가 필요하면 normal document/file/image tool로 추가 inspection할 수 있다.

## 3. Tool evidence IDs

일반 work tool은 `EvidenceTrackingTool` adapter를 통해 실행된다.

실제 성공 result만 turn-local evidence로 등록된다.

```text
tool:1
tool:2
...
```

Tool result object에는 해당 `evidence_id`가 추가되어 모델이 이후 scratchpad source로 사용할 수 있다.

Failed tool execution은 evidence ID를 만들지 않는다.

Evidence ID 생성 여부를 tool/error 문자열 heuristic으로 판단하지 않는다. Adapter가 감싼 actual successful tool execution만 source가 된다.

## 4. Model-managed scratchpad

Scratchpad 작성과 갱신은 별도 구조화된 work tool이다.

새 항목 생성:

```text
scratchpad_put(
  content,
  source_ids=[attachment/tool evidence ids]
)
```

기존 current-turn 항목 갱신:

```text
scratchpad_update(
  scratchpad_id="scratchpad:1",
  content,
  source_ids=[attachment/tool evidence ids]
)
```

Framework는 모든 `source_ids`가 같은 turn의 실제 evidence registry에 존재하는지 검증한다.

`scratchpad_put` 성공 시:

```text
scratchpad:1
scratchpad:2
...
```

형태의 turn-local ID를 만든다.

`scratchpad_update`는 기존 current-turn ID가 실제 존재해야 하며 ID 자체는 바꾸지 않는다. Content와 evidence source set만 교체한다. 존재하지 않는 ID를 update로 암묵 생성하지 않는다.

Scratchpad item은 concise working memory이며 durable semantic graph가 아니다.

현재 구현은 scratchpad item을 다른 scratchpad item의 source로 체이닝하지 않는다. Source는 실제 attachment/tool evidence여야 한다.

## 5. Final graph mutation selection

Final answer의 memory mutation은 필요할 때만 다음 필드를 가진다.

```text
scratchpad_ids: ["scratchpad:1"]
```

`FinalMemoryExecutor`는 각 ID가 현재 turn `ScratchpadRegistry`에 실제 존재하는지 검증한다.

존재하지 않는 ID, 다른 turn ID, 임의 fabricated ID는 `ModelContractError`다.

선택된 scratchpad item만 해당 mutation의 `MemoryTurnScope.evidence_context`에 들어간다.

현재 Phase 4에서는 이 선택된 evidence context를 기존 `graph_provenance.source_text`에 포함한다. Stable raw-source foreign key/reference 구조는 Phase 5에서 추가한다.

## 6. No automatic graph copy

다음은 graph에 자동 저장하지 않는다.

- attachment evidence 전체
- normal tool result 전체
- scratchpad 전체

Graph에는 기존과 동일하게 final memory mutation이 선택한 semantic relation만 들어간다.

Scratchpad는 relation 자체가 아니라 선택된 mutation의 evidence context로만 작용한다.

## 7. Lifetime

Attachment evidence registry와 scratchpad registry는 `turn_id` scope다.

Lifecycle가 completed/failed 어느 쪽으로 끝나도 wrapper `finally`에서 해당 turn registry를 제거한다.

따라서 scratchpad는 다음 turn으로 암묵적으로 carry-over되지 않는다. 장기 continuity는 raw conversation history와 semantic graph가 담당한다.

## 8. Failure visibility

다음은 fallback으로 숨기지 않는다.

- missing attachment -> file failure
- invalid document/image -> parse/verification failure
- text decode failure -> decode failure
- image model failure -> model failure
- unknown evidence ID -> model contract failure
- unknown scratchpad ID -> model contract failure
- `scratchpad_update` on unknown current-turn ID -> model contract failure
- evidence-tracked tool returning a non-object result -> tool contract failure

Phase 4는 evidence와 working memory를 추가하지만 기존 fail-visible 원칙을 완화하지 않는다.
