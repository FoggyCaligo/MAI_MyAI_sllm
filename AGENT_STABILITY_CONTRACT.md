# Mai Agent Stability Contract

이 문서는 MK4에서 검증된 agent 안정성 패턴을 현재 Mai의 single-agent/deferred-tool/fail-visible 계약에 맞게 이식한 구조를 정의한다.

## 1. Successful action dedup

현재 turn의 tool result message에 이미 성공한 exact structured action이 존재하면 같은 action을 다시 실행하지 않는다.

Identity:

```text
tool name + canonical JSON arguments
```

문자열 의미, path 유사도, command 유사도, 사용자 의도를 비교하지 않는다.

Duplicate가 모델에서 다시 나오면 Framework/model boundary는 해당 tool variant를 그 재요청 schema에서 제거하고 `duplicate_successful_action` guard context를 추가한 뒤 모델에게 다른 구조화 action을 선택하게 한다.

이 과정에서 성공 결과를 재실행하거나 cached success로 위장하지 않는다. 해당 tool을 schema에서 하나씩 제거하므로 global retry cap 없이도 structural progress가 발생한다.

## 2. Structured autonomy retry

Final answer schema에는 다음 outcome을 둔다.

```text
completed
blocked
```

Autonomy retry는 다음 조건을 모두 만족할 때만 발생한다.

- model outcome이 구조적으로 `blocked`
- 현재 schema에 실제 tool action capability가 존재
- 현재 model-visible tool history에 real execution failure가 없음
- 이 structured call에서 autonomy reconsideration을 아직 수행하지 않음

Framework는 답변 문자열에서 `못`, `불가능`, `권한` 같은 표현을 검색하지 않는다.

Retry는 MK4의 one structural second chance 원칙을 따른다. 두 번째 결과가 다시 blocked면 이를 강제로 성공으로 바꾸지 않는다.

## 3. Web evidence grounding

현재 turn model messages에 실제 `latest_search` 또는 `web_research` result가 있고 model이 completed answer를 제안하면 answer release 전에 grounding review를 수행한다.

Grounding review 입력:

- proposed answer
- actual current-turn web evidence catalog

Evidence IDs are Framework-created structural references into actual tool results.

Grounding review output은 둘 중 하나뿐이다.

```text
accept + evidence_ids[]
needs_more_evidence + reason
```

Review model은 final answer text를 다시 작성하지 않는다.

### accept

- selected evidence ID가 actual catalog에 있는지 Framework가 검증한다.
- 검증되면 original proposed answer와 original memory mutation plan을 그대로 agent에 반환한다.

### needs_more_evidence

- 해당 internal retry schema에서 answer variant를 제거한다.
- model은 추가 tool/manual/memory inspection action 중 하나를 선택해야 한다.
- Framework가 검색 query나 추가 evidence 의미를 대신 결정하지 않는다.

추가로 사용할 수 있는 non-answer action이 없으면 contract failure로 명확히 실패한다. 부족한 evidence를 일반 지식이나 guessed source로 보충하지 않는다.

## 4. Model call cost

이 안정성 계층은 평범한 non-web completed turn에 model call을 추가하지 않는다.

추가 호출은 조건부다.

- duplicate successful action: duplicate가 실제로 발생했을 때
- autonomy retry: blocked-without-failure일 때 최대 한 번
- grounding review: current turn에 web evidence가 있고 completed answer가 제안되었을 때

따라서 `안녕?` 같은 일반 conversation path는 기존 one-round final answer 가능성을 유지한다.

## 5. Error philosophy

- actual tool exceptions remain failures
- invalid grounding evidence IDs are contract failures
- schema exhaustion is a contract failure
- no automatic tool substitution
- no hidden fallback answer synthesis
- no string-based semantic routing
