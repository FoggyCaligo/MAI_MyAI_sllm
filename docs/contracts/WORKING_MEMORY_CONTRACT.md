# Mai Attachment / Scratchpad Working-Memory Contract

이 문서는 attachment evidence와 turn-local scratchpad 계약을 정의한다.

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

Context 비대화를 막기 위해 automatic extraction은 명시적인 character budget을 가진다. Budget으로 읽지 못한 첨부는 성공으로 추측하지 않고 `not_loaded_context_budget` 상태를 모델에 보여준다.

Owner는 current-turn attachment path provenance를 통해 normal file/document/image tool로 추가 inspection할 수 있다.

Trial은 host file/document/image work tool을 받지 않는다. 대신 자기 authenticated upload directory에 직접 올린 첨부파일만 automatic attachment evidence로 읽거나 분석할 수 있다. 다른 계정 upload path 또는 임의 host path는 허용하지 않는다.

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

Tool evidence의 durable provenance kind도 tool-name 문자열을 해석해서 정하지 않는다. Tool adapter가 `web_evidence`, `file_evidence` 같은 source kind를 구조적으로 선언하고, 선언이 없는 일반 tool result는 `tool_operation`으로 남는다.

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

Production graph source store가 연결된 경우, 선택된 scratchpad와 그 scratchpad가 실제로 참조한 attachment/tool/web evidence는 durable `SourceRecord`로 `graph.sqlite3`에 저장되고 해당 node/edge와 stable source link로 연결된다.

이때 raw evidence 전체를 `graph_provenance.source_text`에 다시 복제하지 않는다. Legacy provenance row에는 source reference marker만 남기고 실제 원문은 source store에서 관리한다.

Scratchpad를 선택하지 않은 직접 대화 기반 memory mutation은 current user message와 fixed assistant answer를 source로 보존할 수 있다. Assistant source는 생성 사실 자체와 외부 세계의 factual truth를 구분하기 위해 unverified metadata를 가진다.

상세 source/provenance 조회 계약은 `GRAPH_SOURCE_CONTRACT.md`를 따른다.

## 6. No automatic graph copy

다음은 graph semantic node/edge로 자동 복제하지 않는다.

- attachment evidence 전체
- normal tool result 전체
- scratchpad 전체
- raw chat transcript 전체

Graph에는 final memory mutation이 선택한 semantic relation만 들어간다.

Durable source store 역시 모든 evidence를 자동 수집하는 archive가 아니다. **실제로 장기 memory mutation의 근거로 채택된 source만** 보존한다.

## 7. Lifetime

Attachment evidence registry와 scratchpad registry는 `turn_id` scope다.

Lifecycle가 completed/failed 어느 쪽으로 끝나도 wrapper `finally`에서 해당 turn registry를 제거한다.

따라서 scratchpad는 다음 turn으로 암묵적으로 carry-over되지 않는다. 장기 continuity는 raw conversation history와 semantic graph가 담당한다.

단, final memory mutation이 채택한 scratchpad/evidence source는 graph source store에 durable copy/reference로 승격되므로 turn-local registry가 제거되어도 해당 장기기억의 근거는 유지된다.

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
- unsupported durable source kind -> source contract failure
- stable source identity collision -> explicit source failure

Attachment/scratchpad/source 계층은 기존 fail-visible 원칙을 완화하지 않는다.
