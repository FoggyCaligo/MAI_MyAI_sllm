# Graph Source / Confidence Contract

## Purpose

Semantic graph는 장기적으로 필요한 의미 관계를 저장한다. Raw chat/tool/file/web 전체를 graph node/edge로 복제하지 않는다.

장기 graph mutation이 실제로 근거로 채택한 source만 `graph.sqlite3` 내부의 source store에 보존하고 node/edge와 구조적으로 연결한다.

따라서 `graph.sqlite3`는 다음을 함께 보관할 수 있다.

- semantic nodes / edges
- reinforcement (`support_count`)
- conflict/revision signal
- durable source evidence
- graph target -> source reference

전체 대화 transcript/session/job은 여전히 `chat.sqlite3` 역할이다.

## Source kinds

Framework가 구조적 execution path로 source kind를 지정한다.

- `user_message`
- `assistant_message`
- `web_evidence`
- `file_evidence`
- `tool_operation`
- `scratchpad`

문장 내용을 검사해 source kind를 추론하지 않는다.

Assistant source는 `assistant가 이 문장을 생성했다`는 provenance를 보존하며 metadata에 `factual_status=unverified`를 둔다. Assistant 발화 자체가 외부 세계의 사실임을 의미하지 않는다.

## Source selection

Scratchpad를 사용하지 않은 일반 memory mutation은 current user message와 fixed assistant answer를 source로 보존할 수 있다.

Scratchpad-backed mutation은 선택된 scratchpad와 그 scratchpad가 실제로 참조한 current-turn attachment/tool evidence를 보존한다. 이 경우 current user message를 자동 최고-confidence source로 추가하지 않는다. 질문이 존재했다는 사실과 tool/web/file에서 얻은 factual evidence를 구분하기 위해서다.

존재하지 않는 scratchpad/evidence ID를 source로 사용할 수 없다.

## Stable source identity

Source identity는 `(user_id, turn_id, source_kind, source_key)`로 고정된다.

동일 identity가 이미 존재하면 같은 content/metadata일 때만 재사용한다. 같은 stable identity에 다른 content가 들어오면 collision failure를 명확히 발생시킨다.

## Confidence

Default recall에 상세 provenance 전체를 넣지 않는다.

Edge에는 기본적으로 다음 compact metadata만 추가한다.

```text
confidence
source_kind
```

Confidence는 Framework가 이미 알고 있는 구조적 신호만 사용한다.

- source kind의 base reliability
- edge `support_count`
- revision/conflict count
- source kind의 stability

문장 의미, 사람 이름, 특정 키워드 등을 문자열 heuristic으로 해석해 confidence를 정하지 않는다.

현재 base reliability/stability는 runtime contract의 명시적 source-kind policy이며 `GraphSourceStore`에 정의한다. 동일 edge가 반복 확인되면 confidence/stability가 제한된 범위에서 강화되고, `revise_memory`는 conflict signal을 증가시킨다.

## Lazy disclosure

### Level 1 — normal recall

```text
semantic edge + confidence + source_kind
```

### Level 2 — `memory_source_summary`

현재 turn에서 실제로 recall된 node/edge만 대상으로 한다.

반환 내용에는 raw source body를 넣지 않는다.

- confidence
- source_kind
- support_count
- conflict_count
- stability
- source IDs
- compact source metadata

Summary에서 실제로 확인한 source ID만 Level 3 읽기 scope에 들어간다.

### Level 3 — `memory_source_read`

`memory_source_summary`로 현재 turn에 노출된 source ID만 읽을 수 있다.

Raw content는 `start` / `limit`으로 pagination하며 한 번에 무제한 context를 주입하지 않는다.

## Ownership

Graph source와 graph target은 동일 `user_id` scope를 공유한다.

다른 user의 source ID/node ID/edge ID를 조회하거나 연결할 수 없다.

없는 source를 다른 문자열로 추측 복원하지 않는다.

## Failure visibility

- unsupported source kind -> explicit failure
- stable source identity collision -> explicit failure
- foreign/missing source -> scope/not-found failure
- invalid recalled target -> model contract failure
- uninspected source ID raw read -> model contract failure

Source store는 fallback으로 기존 provenance 문자열을 조작하여 성공한 것처럼 만들지 않는다.
