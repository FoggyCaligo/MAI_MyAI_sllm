# Tool Requirement Preflight

이 문서는 MAI production runtime의 tool requirement preflight 구조를 기록한다.

## 목적

Main agent가 final answer를 만들기 전에, 이번 요청을 완료하려면 어떤 native tool의 실제 실행 결과가 반드시 필요한지 미리 고정한다.

Preflight는 semantic router이지만 문자열 heuristic은 사용하지 않는다. 사용자의 최신 요청, 최근 대화, 현재 등록된 tool 정의를 모델이 보고 판단한다.

## 실행 방식

Preflight는 main agent와 같은 선택 모델을 사용한다.

```text
think=False
tools=()
structured response_format
```

현재 등록 tool 전체를 한 번에 판단하지 않는다. Registry 순서를 유지한 채 **최대 5개씩 batch로 나누고**, 각 batch를 별도 structured-output 호출로 판단한다.

예를 들어 12개 tool이 노출되어 있으면 호출 단위는 다음과 같다.

```text
batch 1: tool 0~4
batch 2: tool 5~9
batch 3: tool 10~11
```

각 batch에는 그 batch의 tool 정의만 `available_tools`와 response schema에 포함된다. 한 batch의 판단 결과가 다른 batch의 schema를 바꾸지는 않는다.

모든 batch classification은 같은 preflight 단계에서 **동시에 시작**한다. Runtime은 모든 batch 결과가 성공적으로 돌아온 뒤에만 decision을 합친다. 어느 batch 하나라도 실패하면 전체 preflight가 실패한다.

`recent_dialogue`는 reference를 해석하기 위한 context다. 이전 사실이나 이전 tool/research 성공을 자동으로 증명하는 evidence로 취급하지 않는다.

## 동적 boolean response schema

Runtime은 각 batch의 `ToolDefinition` 목록을 순회해 JSON Schema를 동적으로 만든다.

각 등록 tool은 다음 형태의 required property가 된다.

```json
{
  "type": "boolean",
  "description": "ToolDefinition.description"
}
```

즉 모델이 tool name을 생성하는 방식이 아니다. Tool name은 schema에 이미 고정되어 있고 모델은 각 tool에 `true` 또는 `false`만 채운다.

예를 들어 한 batch에 다음 세 tool이 들어 있다면:

```text
web_search
file_search
calculator
```

모델이 채우는 결과의 의미적 형태는 다음과 같다.

```json
{
  "web_search": true,
  "file_search": false,
  "calculator": false
}
```

Schema는 해당 batch의 모든 tool name을 `required`에 포함하고 `additionalProperties=false`를 사용한다.

따라서:

- batch 내부 tool 누락은 contract violation
- unknown property는 contract violation
- boolean이 아닌 값은 contract violation
- 새 tool이 registry에 추가되면 preflight code 수정 없이 batch 분할 및 schema에 자동 반영
- 각 tool 설명은 별도 하드코딩 표가 아니라 기존 `ToolDefinition.description`을 그대로 재사용

## 판단 계약

각 boolean은 해당 tool의 실행 결과가 valid final answer 전에 필요한지를 뜻한다.

사용자가 external/local source의 search, inspect, verify, compare, re-check를 요청했다면 model knowledge만으로 그 evidence 요구를 충족했다고 보지 않는다.

필요한 path, identifier, target이 아직 확립되지 않아 다른 tool이 먼저 찾아야 한다면 discovery tool과 operation tool을 모두 required로 판단한다.

현재 시점과 비교해야 하는 요청은 current moment가 이미 확립되어 있지 않다면 available current-time tool을 required로 판단할 수 있다.

Optional detail만을 위해 tool을 강제하지 않는다.

## Frozen requirements

각 batch의 structured result에서 `true`인 decision을 모두 합친 뒤 한 번 `FrozenToolRequirements`로 변환한다.

이 집합은 해당 run 동안 변하지 않는다.

Main agent가 required tool의 handler execution result 없이 final을 시도하면 runtime은 final을 거부하고 아직 missing인 required tool만 노출하는 correction round를 제공한다.

## 실패 원칙

어느 batch든 structured output이 schema를 위반하거나 model call 자체가 실패하면 preflight 전체가 실패한다.

실패한 batch를 `false`로 간주하거나 empty requirement로 바꾸는 fallback은 두지 않는다. 문자열 비교나 unknown output 의미 추측으로 실패를 숨기지 않는다.
