# MAI MyAI sLLM

MAI MyAI sLLM은 Ollama에서 실행되는 소형/중형 로컬 언어모델을 **개인 PC 전체를 다룰 수 있는 지속형 개인 에이전트**로 사용하기 위한 런타임 프로젝트다.

이 저장소는 기존 구현을 이어붙이는 방식이 아니라, 실패했던 이전 Agent/Memory 결합 구조를 폐기하고 **Ollama native tool calling을 중심으로 다시 시작하는 새 기반**이다. 목표는 Ornith-1.5 계열과 앞으로 등장할 Ollama tool-capable sLLM을 모델 교체만으로 사용할 수 있게 하고, MAI/MACHI MK4에서 발전시킨 그래프 기억 원리는 독립적인 memory subsystem으로 재구성하는 것이다.

현재 커밋은 구현 완성본이 아니라 **새 아키텍처의 골격과 계약을 확정하는 초기 scaffold**다.

## 핵심 목표

1. Ollama의 native `tools` / `tool_calls` 프로토콜을 그대로 사용한다.
2. 특정 모델 전용 JSON 응답 규약이나 문자열 기반 tool-call 파서를 만들지 않는다.
3. Agent loop, tool 실행, 오류 처리, 반복 방지는 MAI runtime이 담당한다.
4. 기억은 단순한 tool 하나가 아니라 Agent와 병렬로 존재하는 독립 cognitive subsystem으로 둔다.
5. 자동 recall과 명시적 memory tool을 함께 제공한다.
6. 로컬 파일, 문서, 이미지, 코드, 터미널, 웹 등의 도구를 하나의 registry에서 native tool로 노출한다.
7. 파일/터미널 도구는 workspace에 제한하지 않고, MAI 프로세스를 실행한 Windows 사용자 계정이 접근 가능한 로컬 PC 전체를 다룰 수 있게 한다.
8. 실패를 숨기지 않는다. 필수 계약 위반, 파일 부재, 명령 실패, timeout, 권한 오류는 구조적으로 드러나야 한다.
9. 의미 판단을 문자열 휴리스틱으로 우회하지 않는다. 모델이 의미를 판단하고 framework는 구조와 실행 계약을 강제한다.

---

## 왜 새로 만드는가

기존 구현은 Agent orchestration, memory, tool routing, 응답 형식이 강하게 결합되어 있었다. 특히 모델에게 자체 JSON 형식을 작성하게 한 뒤 이를 파싱하는 방식은 모델별 native tool 학습을 충분히 활용하지 못하고, parser/guard/fallback이 계속 누적되는 문제가 있었다.

새 구조에서는 경계를 명확히 나눈다.

```text
Ollama                 : inference / thinking / native tool_calls
MAI Agent Runtime      : loop / execution / validation / guards / recovery
MAI Memory Runtime     : store / activation / recall / extraction / graph
Tool Implementations   : filesystem / terminal / web / document / image / code
```

모델은 바뀔 수 있지만 Agent와 Memory의 계약은 유지되는 것이 목표다.

---

## 전체 실행 흐름

```text
User
 │
 ▼
Conversation Runtime
 │
 ├─ raw utterance 기록
 ├─ 이전 activation 상태 로드
 ├─ 현재 발화 activation
 └─ automatic recall
 │
 ▼
MemoryContext
 │
 ▼
Agent Runtime
 │
 ├─ Ollama chat(messages, tools, think)
 │        │
 │        ├─ message.content
 │        ├─ message.thinking
 │        └─ message.tool_calls
 │
 ├─ tool call 검증
 ├─ tool 실행
 ├─ role=tool 결과 추가
 ├─ guard / progress 검사
 └─ 필요한 동안 반복
 │
 ▼
Final answer
 │
 ▼
Memory extraction
 │
 ▼
Graph mutation
```

### 중요한 원칙

`memory_search`를 한 번 호출해야만 기억을 사용할 수 있는 구조로 만들지 않는다. 사람에게 어떤 기억이 먼저 자연스럽게 떠오르는 것처럼, 각 user turn이 시작될 때 memory runtime이 관련 영역을 자동 recall하여 Agent의 첫 모델 호출에 이미 포함시킨다.

반대로 자동 recall만으로 부족한 깊은 탐색은 native memory tool로 수행한다.

```text
automatic recall = 자연스럽게 떠오르는 기억
memory tools      = 의도적으로 기억을 더 뒤지는 행동
```

---

## Agent Runtime

`mai/agent`는 Ollama와 tool system 사이의 실행 책임을 가진다.

Agent가 담당하는 것:

- multi-round model/tool loop
- native `tool_calls` 수신
- tool registry 조회
- arguments schema validation
- tool 실행 및 결과 기록
- tool exception 보존
- timeout 처리
- unknown tool 처리
- 동일 tool + 동일 arguments 반복 감지
- 동일 terminal command 반복 감지
- global maximum round
- 모델이 더 이상 tool을 호출하지 않을 때 최종 응답 종료
- context 및 tool-result 크기 관리

Agent가 담당하지 않는 것:

- 문자열 패턴으로 사용자 의도 추측
- 실패한 tool을 다른 tool로 몰래 대체
- 모델이 요청하지 않은 side effect 실행
- 오류를 성공 응답으로 변환
- memory 내부 graph 의미 결정

### 기본 guard

초기 기본값은 다음을 목표로 한다.

```text
AGENT_MAX_ROUNDS=30
AGENT_MAX_IDENTICAL_CALLS=3
TOOL_TIMEOUT_SECONDS=60
TERMINAL_TIMEOUT_SECONDS=120
```

동일 호출은 `(tool_name, normalized_arguments)`의 구조적 동일성으로 판단한다. 사용자 문장이나 tool 이름의 의미를 문자열 휴리스틱으로 해석하여 route를 결정하지 않는다.

---

## Ollama 계층

`mai/llm`은 최대한 얇게 유지한다.

입력:

- `messages`
- native `tools`
- model name
- `think`
- Ollama options

출력:

- assistant content
- thinking
- native tool calls
- 모델/HTTP 오류

Ornith 전용 runtime을 만드는 것이 아니다. `ornith-1.5:9b`는 초기 기본 후보일 뿐이고, 향후 Ollama native tool calling을 정상 지원하는 모델이면 동일 Agent Runtime에 연결할 수 있어야 한다.

모델별 차이가 생기더라도 가능한 한 `mai/llm` adapter 내부에 국한한다.

---

## Memory Runtime

Memory는 Agent의 부가기능이 아니라 독립 subsystem이다.

외부에서는 주로 다음 두 lifecycle entry point를 사용한다.

```text
memory_runtime.begin_turn(...)
memory_runtime.finish_turn(...)
```

내부 구조는 다음과 같이 분리한다.

### Store

사실과 해석을 섞지 않고 저장 계약을 담당한다.

- raw utterance 기록
- node 생성
- edge/relation 생성
- provenance 기록
- source/session 연결

### Graph

SQLite 기반 장기 기억의 물리적 저장과 graph 접근 API를 담당한다.

Agent가 SQL schema를 직접 알지 않게 한다.

예상 API:

```text
get_node
get_neighbors
create_node
create_relation
find_relations
get_subgraph
```

### Activation

MK4에서 발전시킨 기억 원리를 유지한다.

현재 턴에서 직접 활성화된 영역, 직전 턴에서 잔존한 활성 영역, 관계를 통해 이어지는 주변 영역을 이용해 **이번 턴의 working-memory 후보**를 만든다.

Activation은 단순히 전체 DB에서 매번 전역 검색하는 구조가 아니다. 큰 DB에서도 현재 활성 영역 주변을 중심으로 움직일 수 있어야 한다.

### Recall

Activation 후보와 현재 query를 바탕으로 모델에게 실제 제공할 작은 subgraph를 만든다.

단순 문장 배열보다는 구조화된 memory context를 목표로 한다.

예:

```json
{
  "focus": {
    "node_type": "project",
    "label": "MAI"
  },
  "relations": [
    {"relation": "uses", "target": "graph memory"},
    {"relation": "runs_with", "target": "Ollama"}
  ],
  "provenance": {...}
}
```

### Extraction

Agent의 최종 답변과 사용자 발화를 바탕으로 장기 기억으로 승격할 내용을 graph mutation으로 변환한다.

이 과정은 Agent의 답변 생성 loop와 분리한다. Main Agent가 한 응답에서 답변, graph mutation, tool routing을 동시에 책임지게 하지 않는다.

Memory model은 처음에는 main model과 같을 수 있지만 interface는 별도로 둔다.

```text
MAIN_MODEL=ornith-1.5:9b
MEMORY_MODEL=ornith-1.5:9b
```

향후 더 작은 전용 모델로 교체할 수 있어야 한다.

---

## Memory Tools

자동 recall과 별개로 Agent에 다음과 같은 native tool을 제공할 예정이다.

```text
memory_search
memory_get_node
memory_get_relations
memory_expand
memory_get_source
```

이 tool들은 Memory Runtime 내부 API를 호출하며, Agent가 DB를 직접 읽거나 SQL을 생성하는 방식으로 만들지 않는다.

예를 들어 사용자가 과거 프로젝트 대화를 넓게 비교해 달라고 하면 Agent는 자동 recall된 일부 기억을 출발점으로 `memory_search` 또는 `memory_expand`를 추가 호출할 수 있다.

---

## Short-term / Long-term 분리

```text
Short-term
 ├─ recent messages
 ├─ current activation
 ├─ tool history
 └─ working memory

Long-term
 └─ graph memory
```

대화 history 전체를 graph memory와 동일시하지 않는다.

Tool result 역시 기본적으로 working context에만 존재한다. 모든 터미널 stdout, 파일 본문, 웹 검색 결과를 자동으로 장기기억에 넣지 않는다. 지속적으로 유용한 정보만 extraction 단계에서 graph로 승격한다.

---

## Tool Registry

모든 도구는 하나의 구조적 registry를 통해 Ollama native tool schema로 노출한다.

예정 범주:

```text
memory
filesystem
terminal
code
web
document
image
```

Tool implementation과 tool exposure를 분리하여, runtime은 이름을 문자열로 해석해서 특별 취급하지 않고 registry metadata와 schema를 사용한다.

---

## PC 전체 접근 모델

MAI는 workspace sandbox형 coding agent가 아니라 **사용자의 로컬 PC 전체를 다루는 개인 에이전트**를 목표로 한다.

따라서 filesystem/terminal의 기본 정책은 다음과 같다.

> MAI 프로세스를 실행한 OS 사용자 계정이 접근 가능한 범위라면 repo 밖도 읽기/쓰기/실행할 수 있다.

예:

```text
C:\Users\...
D:\...
다른 Git repository
Desktop / Documents / Downloads
설치된 프로그램
PATH의 CLI
PowerShell / cmd / git / python / ollama
```

관리자 권한이 필요한 작업은 MAI가 관리자 권한으로 실행되지 않았다면 그대로 권한 오류가 발생해야 한다. UAC를 우회하거나 오류를 감추는 로직은 만들지 않는다.

상대경로는 현재 session/runtime cwd 기준으로 해석하고, 절대경로는 그대로 허용한다.

---

## Filesystem Tools

계획된 기본 파일 도구:

```text
file_list
file_search
file_read
file_write
file_create
file_delete
file_move
file_copy
```

기존 MK4가 제공하던 파일 탐색/읽기/작성 능력은 새 native tool registry 위에서 재구성한다.

중요한 차이는 Ollama가 파일을 직접 읽는 것이 아니라, 모델이 native tool call을 생성하고 MAI의 Python tool implementation이 실제 OS 작업을 수행한다는 점이다.

---

## Terminal Tool

터미널 역시 Ollama 내부 기능이 아니다.

예정 계약:

```text
terminal_run(command, cwd=None, timeout=None)
```

실제 실행 권한은 MAI Python process의 OS 권한과 동일하다.

반환값에는 최소한 다음이 보존되어야 한다.

```text
stdout
stderr
returncode
timed_out
cwd
```

명령 실패를 성공 문자열로 변환하지 않는다.

---

## Document / Image Tools

Ollama native tool calling은 `document_read`, `image_read` 같은 파일 도구를 자체 제공하지 않는다. native tool calling은 **외부 함수 호출 프로토콜**이다.

따라서 MAI에서 구현한다.

### Documents

`document_read`가 파일 유형에 맞는 parser를 사용하여 PDF/DOCX/XLSX 등의 텍스트와 구조를 반환하도록 한다.

필요한 경우 이미지 기반 페이지는 vision layer로 넘길 수 있다.

### Images

`image_read`는 설정된 vision model을 통해 이미지 내용을 해석할 수 있게 한다.

Main model과 vision model은 독립 설정을 유지한다.

```text
MAIN_MODEL=ornith-1.5:9b
VISION_MODEL=<vision capable model>
```

향후 main model이 안정적인 vision을 제공하더라도 tool contract 자체는 유지한다.

---

## 오류 처리 원칙

새 runtime에서 실패는 정상적인 데이터다.

예:

```json
{
  "ok": false,
  "error": "file_not_found",
  "path": "..."
}
```

또는 terminal의 non-zero exit code처럼 원래 시스템이 제공하는 실패 신호를 보존한다.

금지하는 방식:

- 오류 메시지 문자열을 보고 임시 분기
- 실패를 다른 성공 tool result로 바꾸기
- 모델이 잘못 호출한 tool을 framework가 의미 추측하여 다른 tool로 치환
- 필수 인자 누락을 임의 기본값으로 메우기
- final response 계약을 helper/fallback으로 우회

복구 가능한 실패라면 모델이 실제 실패 결과를 관찰한 뒤 다음 native tool call을 선택한다.

복구가 불가능하면 최종 응답도 실패 사실을 드러낸다.

---

## 디렉터리 구조

```text
mai/
├─ agent/
│  ├─ runtime.py
│  ├─ loop.py
│  ├─ guards.py
│  └─ context.py
│
├─ llm/
│  ├─ ollama.py
│  └─ models.py
│
├─ memory/
│  ├─ runtime.py
│  ├─ graph/
│  │  ├─ repository.py
│  │  ├─ models.py
│  │  └─ schema.py
│  ├─ activation/
│  │  └─ service.py
│  ├─ recall/
│  │  └─ service.py
│  ├─ extraction/
│  │  └─ service.py
│  └─ tools.py
│
├─ tools/
│  ├─ registry.py
│  ├─ filesystem.py
│  ├─ terminal.py
│  ├─ code.py
│  ├─ web.py
│  ├─ documents.py
│  └─ images.py
│
└─ app/
   └─ runtime.py
```

---

## 구현 순서

초기 구현은 다음 순서를 권장한다.

1. Ollama native chat/tool adapter
2. Tool registry 및 native schema 생성
3. 최소 Agent loop
4. loop/timeout/error guards
5. filesystem + terminal tools
6. conversation/runtime context
7. graph repository와 raw utterance store
8. activation + automatic recall
9. memory native tools
10. extraction pipeline
11. document/image/code/web tools
12. UI/server layer
13. 모델별 호환성 테스트와 A/B 평가

기능을 한꺼번에 prompt로 보정하지 않고 각 계층의 계약을 테스트 가능한 단위로 만든다.

---

## 초기 모델

첫 검증 후보는 `ornith-1.5:9b`다.

다만 프로젝트 목적은 Ornith 전용 애플리케이션이 아니다. 모델 교체가 runtime 재설계를 요구하지 않는 구조가 목표다.

`.env` 예시:

```env
OLLAMA_HOST=http://127.0.0.1:11434
MAIN_MODEL=ornith-1.5:9b
MEMORY_MODEL=ornith-1.5:9b
OLLAMA_THINK=true
```

---

## 현재 상태

현재 단계는 **architecture reset / scaffold**다.

이 커밋에서 중요한 것은 기능 수가 아니라 경계다.

- Ollama native tool calling을 표준 model-tool 인터페이스로 사용한다.
- Agent Runtime과 Memory Runtime을 분리한다.
- Memory는 자동 recall + explicit memory tools의 이중 구조로 만든다.
- PC 전체 접근은 tool implementation의 정식 capability로 둔다.
- 기존 MK4의 기억 원리는 유지하되 기존 Agent JSON protocol과 orchestration 결합은 가져오지 않는다.
- 실패는 숨기지 않는다.

다음 단계부터 각 모듈을 이 계약에 맞춰 하나씩 실제 구현한다.
