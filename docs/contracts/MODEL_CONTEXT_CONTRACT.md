# Mai Model Context Contract

이 문서는 모델에 주입되는 단기 문맥과 graph semantic memory의 역할을 구분한다.

## 1. Raw conversation history

최근 대화는 `chat_messages`의 원문 user/assistant text를 사용한다.

- 목적: 바로 이전 대화의 continuity 유지
- 저장 형식: raw text
- model injection: 최근 10개 message
- message당 최대 3000 characters
- raw chat log 자체를 semantic graph로 간주하지 않는다.
- model에게 주입하기 위한 truncation은 장기 저장 원문을 변경하지 않는다.

즉 최근 대화와 graph memory는 서로 대체하지 않는다.

```text
recent conversation = short-term conversational context
semantic graph      = durable semantic memory
```

## 2. Semantic graph memory

Graph에는 final answer action의 `memory_mutations`에서 모델이 선택한 semantic relation만 기록한다.

Raw chat history 전체를 자동으로 graph edge 집합으로 복제하지 않는다.

Framework는 graph의 scope, ownership, transaction을 강제하지만 어떤 semantic fact/relation을 남길지는 모델이 결정한다.

## 3. Recent tool operations

완료된 turn의 work events는 다음 turn의 작업 continuity를 위해 compact form으로 저장한다.

- model injection: 최근 5개 operation
- 원본 work event의 성공/실패 의미를 바꾸지 않는다.
- compact copy는 다음 model context 전용이다.
- compacting은 tool result를 성공으로 바꾸거나 오류를 숨기지 않는다.

## 4. Current-turn tool results

현재 agent loop의 실제 tool result는 runtime event에서 원본을 유지한다.

다음 model round에는 context 크기를 제한하기 위한 compact copy를 주입한다.

대표 제한:

- `file_read`, `document_read`: 최대 약 2400-character excerpt
- `terminal_command`: stdout/stderr tail 각 최대 500 characters
- `file_search`, `file_tree`: 최대 60 structural entries
- `file_text_search`: 최대 40 matches
- `code_search`: 최대 20 results
- `web_research`: evidence 최대 약 5000 characters
- generic result: 최대 약 1200-character structural compact form

이 제한은 모델 입력용 representation에만 적용한다. Tool 실행 결과 원본을 변조하지 않는다.

## 5. Current date

매 model request의 system context에 host local date를 다음 형식으로 주입한다.

```text
Current date: YYYY-MM-DD.
```

날짜를 사용해 의미 routing을 하지는 않는다. 모델이 현재 시점을 해석하기 위한 실행 context다.

## 6. Fail-visible rule

Context preparation 자체가 구조 계약을 만족하지 못하면 명확히 실패한다.

예를 들어 current-turn tool message가 structured event로 해석되지 않으면 이를 임의 문자열 요약으로 우회하지 않는다.

## 7. Future scratchpad

Scratchpad는 raw conversation이나 durable graph와 다른 현재 작업용 temporary working memory로 추가한다.

예정 역할:

```text
raw recent chat
+ current tool evidence
+ model-managed scratchpad
        ↓
final answer
        ↓
final graph mutation may reference scratchpad evidence
```

Scratchpad 자체를 자동으로 durable graph에 저장하지 않는다. Durable semantic mutation은 final graph update 시점에 모델이 선택한다.
