# MAI MyAI sLLM

MAI MyAI sLLM은 Ollama에서 실행되는 소형/중형 로컬 언어모델을 **개인 PC 전체를 다룰 수 있는 지속형 개인 에이전트**로 사용하기 위한 런타임 프로젝트다.

이 저장소는 기존 구현을 이어붙이는 방식이 아니라, 실패했던 이전 Agent/Memory 결합 구조를 폐기하고 **Ollama native tool calling을 중심으로 다시 시작하는 새 기반**이다. 목표는 Ornith-1.5 계열과 앞으로 등장할 Ollama tool-capable sLLM을 모델 교체만으로 사용할 수 있게 하고, MAI/MACHI MK4에서 발전시킨 그래프 기억 원리는 독립적인 memory subsystem으로 재구성하는 것이다.

현재 단계에서는 새 아키텍처 골격 위에 **Ollama native adapter가 첫 실제 구현으로 추가된 상태**다. Agent loop, tool registry, filesystem/terminal, memory runtime은 이후 단계에서 이 adapter 위에 구현한다.

---

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
10. 모델 교체가 Agent/Memory 구조 변경으로 이어지지 않게 한다.

---

# 전체 구조 한눈에 보기

```text
                                   ┌───────────────────────┐
                                   │      Local User       │
                                   └───────────┬───────────┘
                                               │
                                               ▼
                                   ┌───────────────────────┐
                                   │ Conversation Runtime  │
                                   └───────────┬───────────┘
                                               │
                            ┌──────────────────┴──────────────────┐
                            ▼                                     ▼
                 ┌─────────────────────┐               ┌─────────────────────┐
                 │   Memory Runtime    │               │    Agent Runtime    │
                 │                     │               │                     │
                 │ store               │               │ loop                │
                 │ activation          │──────────────▶│ guards              │
                 │ recall              │ MemoryContext │ context             │
                 │ extraction          │               │ tool execution      │
                 │ graph               │               └─────────┬───────────┘
                 └─────────┬───────────┘                         │
                           │                                     ▼
                           │                          ┌─────────────────────┐
                           │                          │   Ollama Adapter    │
                           │                          │ messages/tools      │
                           │                          │ thinking/tool_calls │
                           │                          └─────────┬───────────┘
                           │                                    │
                           │                                    ▼
                           │                          ┌─────────────────────┐
                           │                          │   Ollama / Model    │
                           │                          │ Ornith / future LLM │
                           │                          └─────────┬───────────┘
                           │                                    │
                           │                                    ▼
                           │                          ┌─────────────────────┐
                           │                          │    Tool Registry    │
                           │                          └─────────┬───────────┘
                           │                                    │
                           │          ┌──────────────┬──────────┼──────────┬─────────┐
                           │          ▼              ▼          ▼          ▼         ▼
                           │       memory        filesystem  terminal    web    document/
                           │        tools            tools      tools     tools    image/code
                           │
                           └───────────────────────────────────────────────────────────────
```

역할은 다음처럼 고정한다.

```text
Ollama                 : inference / thinking / native tool_calls
MAI Ollama Adapter     : provider protocol translation only
MAI Agent Runtime      : loop / execution / validation / guards / recovery
MAI Memory Runtime     : store / activation / recall / extraction / graph
Tool Registry          : native tool schema + executable binding
Tool Implementations   : filesystem / terminal / web / document / image / code / memory
```

---

# 한 user turn의 전체 실행 순서

```text
User input
   │
   ▼
1. raw utterance 저장
   │
   ▼
2. 이전 activation 상태 로드
   │
   ▼
3. 현재 발화에서 graph activation
   │
   ▼
4. automatic recall
   │
   ▼
5. MemoryContext 생성
   │
   ▼
6. AgentContext에
   ├─ user message
   ├─ recent dialogue
   ├─ working context
   └─ recalled memory
   를 함께 구성
   │
   ▼
7. Ollama native chat
   │
   ├─ content
   ├─ thinking
   └─ tool_calls
   │
   ▼
8. tool_calls가 있으면
   ├─ schema/registry 검증
   ├─ guard 검사
   ├─ 실제 tool 실행
   ├─ role=tool 결과 추가
   └─ 다시 Ollama 호출
   │
   ▼
9. tool_calls 없이 최종 content가 오면 final answer 확정
   │
   ▼
10. memory extraction
   │
   ▼
11. 장기적으로 남길 내용을 graph mutation
   │
   ▼
12. turn 종료
```

기억 자동 recall은 첫 Ollama 호출보다 앞에 있다.

---

# Native tool agent loop

Ollama가 tool을 직접 실행하는 것은 아니다. Ollama는 모델에게 tool schema를 보여주고 모델이 구조화된 `tool_calls`를 생성하도록 한다. 실제 함수 실행과 반복은 MAI Agent Runtime의 책임이다.

```text
Agent Runtime
    │
    ▼
OllamaAdapter.chat(messages, tools)
    │
    ▼
Ollama / Model
    │
    ├────────────── no tool_calls ───────────────┐
    │                                             │
    ▼                                             ▼
message.tool_calls                          message.content
    │                                             │
    ▼                                             ▼
Tool Registry                                Final answer
    │
    ▼
실제 함수 실행
    │
    ▼
role="tool" result를 messages에 추가
    │
    └───────────────────▶ 다시 Ollama 호출
```

여러 native tool call이 한 turn에서 병렬로 반환될 수 있으므로 adapter는 tool call을 하나로 축소하지 않는다.

---

# Ollama Adapter

현재 실제 구현이 들어간 첫 계층이다.

```text
mai/llm/models.py
mai/llm/ollama.py
```

## Adapter의 책임

```text
입력
 ├─ messages
 ├─ native tools
 ├─ model name
 ├─ think
 └─ Ollama options

출력
 ├─ content
 ├─ thinking
 ├─ native tool_calls
 └─ normalized assistant_message
```

```text
MAI ChatRequest
      │
      ▼
OllamaAdapter
      │
      ├─ model
      ├─ messages
      ├─ tools
      ├─ think
      ├─ options
      └─ stream=False
      │
      ▼
Ollama native chat
      │
      ▼
message
 ├─ content
 ├─ thinking
 └─ tool_calls
      │
      ▼
MAI ModelTurn
```

Adapter는 tool을 실행하지 않고 Agent loop도 돌리지 않는다. Tool 이름의 의미도 해석하지 않는다. Ollama native response를 MAI 내부의 provider-neutral 구조로 정규화하는 역할만 가진다.

## Thinking

```text
message.thinking  → reasoning trace
message.content   → final answer content
```

MAI는 `<think>...</think>` 문자열을 직접 잘라내는 parser를 만들지 않는다.

Think 설정은 boolean과 모델별 level 값을 모두 전달할 수 있게 한다.

```text
true / false / "low" / "medium" / "high" / "max"
```

## Native tool calls

```text
message.tool_calls[]
 └─ function
     ├─ index
     ├─ name
     └─ arguments
```

MAI 내부:

```text
NativeToolCall
 ├─ name
 ├─ arguments
 └─ index
```

`arguments`가 object가 아니거나 function name이 비어 있는 등 native contract가 깨진 경우 임의 보정하지 않고 `OllamaProtocolError`로 실패시킨다.

## Assistant message 보존

```text
ModelTurn
 ├─ content
 ├─ thinking
 ├─ tool_calls
 └─ assistant_message
```

Agent Runtime은 향후 `assistant_message`를 그대로 history에 추가한 뒤 `role=tool` 결과를 이어 붙인다. 자체 tool-call JSON을 다시 만들 필요가 없다.

---

# Memory와 Agent의 결합 위치

Memory는 Agent의 tool 하나로만 존재하지 않는다.

```text
User
 ↓
MemoryRuntime.begin_turn()
 ↓
automatic activation / recall
 ↓
MemoryContext
 ↓
AgentRuntime.run()
 ↓
final answer
 ↓
MemoryRuntime.finish_turn()
 ↓
extraction / graph mutation
```

```text
automatic recall = 자연스럽게 떠오르는 기억
memory tools      = 의도적으로 기억을 더 뒤지는 행동
```

---

# Memory Runtime

```text
memory/
 ├─ runtime.py
 ├─ graph/
 │   ├─ repository.py
 │   ├─ models.py
 │   └─ schema.py
 ├─ activation/
 │   └─ service.py
 ├─ recall/
 │   └─ service.py
 ├─ extraction/
 │   └─ service.py
 └─ tools.py
```

## Graph / Activation / Recall

```text
현재 턴 직접 활성
        +
직전 턴 잔존 activation
        +
관계로 이어지는 주변 영역
        ↓
working-memory 후보
        ↓
current query와 연결된 local subgraph
        ↓
MemoryContext
        ↓
Agent first model call
```

전체 DB를 매 턴 전역 재해석하지 않고 현재 활성 영역 주변을 중심으로 움직이는 MK4의 원리를 유지한다.

## Extraction

```text
User utterance
      +
Final answer
      +
relevant turn evidence
      ↓
Memory extraction model
      ↓
structured graph mutations
      ↓
Graph repository
```

Agent의 답변/tool loop와 memory graph mutation을 분리한다.

---

# Memory Tools

```text
memory_search
memory_get_node
memory_get_relations
memory_expand
memory_get_source
```

```text
Model
 ↓ native tool_call
Memory Tool
 ↓
Tool Registry
 ↓
Memory Runtime public API
 ↓
Graph / Recall service
 ↓
structured result
 ↓
role=tool
 ↓
Model
```

---

# Short-term / Long-term 분리

```text
Short-term
 ├─ recent messages
 ├─ current activation
 ├─ tool history
 └─ working memory

Long-term
 └─ graph memory
```

```text
file_read / terminal / web result
              ↓
        working context
              ↓
      turn 종료 시 extraction
              ↓
필요한 것만 long-term graph로 승격
```

---

# Tool Registry

```text
ToolDefinition
 ├─ native JSON schema
 ├─ executable binding
 ├─ timeout metadata
 └─ structural metadata
```

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

runtime은 tool 이름 문자열의 의미를 휴리스틱으로 해석해 route를 결정하지 않는다.

---

# PC 전체 접근 모델

```text
MAI Process
   │
   ├─ C:\Users\...
   ├─ D:\...
   ├─ 다른 Git repository
   ├─ Desktop / Documents / Downloads
   ├─ 설치된 프로그램
   └─ PATH에서 실행 가능한 CLI
```

MAI 프로세스를 실행한 OS 사용자 계정이 접근 가능한 범위라면 repo 밖도 읽기/쓰기/실행할 수 있게 한다. 관리자 권한이 필요한 작업은 실제 권한 오류로 실패한다.

---

# Filesystem 흐름

```text
Model
 ↓
file_read({"path": "C:\\Users\\...\\file.txt"})
 ↓
Ollama native tool_call
 ↓
Agent Runtime
 ↓
Tool Registry
 ↓
filesystem implementation
 ↓
OS filesystem
 ↓
structured result
 ↓
role=tool
 ↓
Model
```

절대경로를 정식 입력으로 허용하고 상대경로는 runtime의 `cwd` 기준으로 해석한다.

---

# Terminal 흐름

예정 계약:

```text
terminal_run(command, cwd=None, timeout=None)
```

```text
Model
 ↓
terminal_run(...)
 ↓
Agent Runtime
 ↓
subprocess
 ↓
Windows user account 권한
 ↓
stdout / stderr / returncode
 ↓
role=tool result
 ↓
Model
```

명령 실패를 성공 문자열로 바꾸지 않는다.

---

# Document / Image 흐름

```text
Model
 ↓
document_read(path)
 ↓
Tool Registry
 ↓
PDF / DOCX / XLSX parser
 ↓
text / tables / structure
 ↓
role=tool
 ↓
Model
```

```text
Model
 ↓
image_read(path)
 ↓
Tool Registry
 ↓
Image loader
 ↓
Configured Vision Model
 ↓
structured visual result
 ↓
role=tool
 ↓
Main Agent Model
```

Main model과 vision model은 독립 설정을 유지한다.

---

# 모델 교체 구조

```text
                 ┌─ ornith-1.5:9b
.env model name ─┼─ future qwen
                 ├─ future gemma
                 └─ other Ollama tool-capable model
                        │
                        ▼
                  Ollama Adapter
                        │
                        ▼
                  동일 Agent Runtime
                        │
                        ▼
                  동일 Tool Registry
                        │
                        ▼
                  동일 Memory Runtime
```

Ornith는 초기 기본 후보일 뿐, Ornith 전용 Agent를 만들지 않는다.

---

# Agent Guard / 무한루프 방지

```text
AGENT_MAX_ROUNDS=30
AGENT_MAX_IDENTICAL_CALLS=3
TOOL_TIMEOUT_SECONDS=60
TERMINAL_TIMEOUT_SECONDS=120
```

```text
native tool_call
 ↓
구조 검증
 ↓
(tool_name, normalized_arguments) fingerprint
 ↓
반복 횟수 검사
 ↓
실행
 ↓
진행 상태 기록
 ↓
다음 round
```

PC 전체 접근 권한과 무한 실행 허용은 별개의 문제다. 접근 범위는 넓게 두되 반복/timeout/contract failure는 runtime guard가 구조적으로 막는다.

---

# 오류 처리 원칙

실패는 정상적인 데이터다.

```json
{
  "ok": false,
  "error": "file_not_found",
  "path": "..."
}
```

금지:

```text
오류 문자열을 보고 몰래 성공으로 간주
실패한 tool 대신 framework가 임의의 다른 tool 호출
필수 필드가 없는데 default 값으로 의미를 만들어냄
모델 응답 계약을 regex/string heuristic으로 복구
실패한 작업을 최종 응답에서 완료했다고 표현
```

---

# 현재 파일 골격

```text
mai/
├─ agent/
│  ├─ runtime.py
│  ├─ loop.py
│  ├─ guards.py
│  └─ context.py
├─ llm/
│  ├─ __init__.py
│  ├─ ollama.py
│  └─ models.py
├─ memory/
│  ├─ runtime.py
│  ├─ graph/
│  ├─ activation/
│  ├─ recall/
│  ├─ extraction/
│  └─ tools.py
├─ tools/
│  ├─ registry.py
│  ├─ filesystem.py
│  ├─ terminal.py
│  ├─ code.py
│  ├─ web.py
│  ├─ documents.py
│  └─ images.py
└─ app/
   └─ runtime.py
```

---

# 현재 구현 상태

## 구현됨

```text
[Ollama adapter]
- configurable model / host / think / options
- native messages 전달
- native tools 전달
- message.content 보존
- message.thinking 보존
- parallel native tool_calls 보존
- normalized assistant_message 생성
- malformed native response를 명시적 protocol error로 처리
- test용 client injection 지원
```

## 아직 골격만 존재

```text
Agent Runtime
Tool Registry
Agent Guards
Filesystem / Terminal / Code / Web / Document / Image Tools
Memory Graph / Activation / Recall / Extraction / Memory Tools
Application/UI runtime
```

---

# 구현 순서

```text
1. Ollama Adapter                 ← 현재 구현 완료
       ↓
2. Native Tool Registry
       ↓
3. Minimal Agent Loop
       ↓
4. Agent Guards / Error semantics
       ↓
5. Filesystem + Terminal
       ↓
6. Code / Document / Image / Web
       ↓
7. Memory Graph Repository
       ↓
8. Activation + Automatic Recall
       ↓
9. Explicit Memory Tools
       ↓
10. Memory Extraction
       ↓
11. Conversation/App Runtime
       ↓
12. Live Ornith integration tests
```

---

# 설계 원칙 요약

```text
Model decides meaning.
Framework enforces structure.
Tools perform side effects.
Memory persists cognition.
Failures remain visible.
```

> **Memory는 Agent의 tool이 아니라 Agent가 매 턴 사용하는 독립 cognitive subsystem이고, memory tools는 그 subsystem을 능동적으로 탐색하기 위한 추가 인터페이스다.**

> **Ollama native tool calling은 Agent framework 자체가 아니라 model ↔ tool protocol이며, Agent loop와 실제 실행 책임은 MAI가 가진다.**

> **MAI는 workspace에 갇힌 coding agent가 아니라, 실행한 OS 사용자 계정 범위의 로컬 PC 전체를 다루는 개인 에이전트를 목표로 한다.**
