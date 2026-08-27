# MAI MyAI sLLM

MAI MyAI sLLM은 **그래프 기반 장기기억을 소형 로컬 언어모델(sLLM)에 부여하고, 그 기억을 로컬 PC 도구 사용과 연결하는 개인 에이전트 런타임**이다. 과거 대화를 통째로 쌓는 대신 사용자·프로젝트·사실·선호·정정·출처를 노드와 관계로 축적하고, 현재 대화에서 활성화된 개념 주변의 작은 서브그래프만 모델에 다시 보여주는 것을 핵심 아이디어로 삼는다. 반복 개념을 하나의 노드로 공유해 장기적인 중복을 줄일 가능성이 있고, 특정 개념이 어떤 발화·사실·출처·정정 관계를 통해 형성됐는지를 사람이 읽을 수 있는 구조로 남길 수 있다는 장점이 있다. 반면 그래프 자체가 사고까지 맡으면 응답 파이프라인과 유지보수 비용이 커질 수 있으므로, MACHI MK4에서 정리한 방향처럼 **그래프는 장기기억과 회수에 집중하고 실제 계획·도구 선택·응답 생성은 LLM이 담당**한다. 한 턴이 시작되면 사용자 발화를 저장하고 현재 입력과 이전 활성 영역으로 관련 기억을 자동 recall하여 첫 모델 context에 포함하고, 부족할 때만 모델이 memory tool로 더 탐색하며, 최종 응답 뒤에는 필요한 장기 정보만 그래프에 반영하는 구조를 목표로 한다. 모델과 도구 사이의 통신은 자체 JSON 규약 대신 Ollama native `tools` / `tool_calls`를 사용한다. 세부 개발 계약은 [`WORKING_CONTRACT.md`](WORKING_CONTRACT.md)에 있다.

## 도구

모든 도구는 `ToolRegistry`에 등록되어 Ollama native function schema로 모델에 노출된다. Registry는 이름·설명·Pydantic 입력 모델·실행 함수·timeout이라는 구조적 계약만 관리하며 사용자 문장을 문자열 규칙으로 해석해 tool route를 고르지 않는다. 현재 **Ollama adapter, native Tool Registry, Agent Runtime, structural Agent Guard, PC-wide Filesystem/Terminal tools까지 구현**되어 있다. 파일 계층에는 `file_list`, `file_search`, `file_read`, `file_write`, `file_create`, `file_delete`, `file_move`, `file_copy`가 있으며 Python filesystem API를 사용한다. 절대경로를 정식 입력으로 허용하고 상대경로는 등록 시점의 `cwd` 기준으로 해석하며, repository/workspace confinement를 두지 않아 MAI 프로세스를 실행한 OS 사용자 계정이 접근 가능한 로컬 PC 전체를 대상으로 한다. `file_create`는 기존 파일을 덮어쓰지 않고 실패하며, `file_write`는 기존 파일만 수정한다. `terminal_run`은 로컬 shell에서 command·cwd·timeout을 받아 실행하고 `stdout`, `stderr`, `returncode`, `timed_out`, 실제 `cwd`를 그대로 반환한다. timeout 시에는 Windows에서는 `taskkill /T /F`, POSIX에서는 process group kill을 사용해 자식 프로세스까지 종료한다. non-zero exit code는 성공으로 바꾸지 않고 원래 return code를 보존한다. 이후 `code`, `document_read`, `image_read`, `web`, memory tools를 같은 registry 위에 추가한다.

## 파일 구조와 전체 작동 구조

```text
mai/
├─ llm/
│  ├─ models.py            # provider-neutral request/response types
│  └─ ollama.py            # Ollama native adapter
├─ tools/
│  ├─ registry.py          # native schema + strict validation + execution
│  ├─ local.py             # implemented local-PC tool bundle
│  ├─ filesystem.py        # implemented PC-wide file operations
│  ├─ terminal.py          # implemented local shell execution
│  ├─ code.py              # planned
│  ├─ documents.py         # planned
│  ├─ images.py            # planned
│  └─ web.py               # planned
├─ agent/
│  ├─ runtime.py           # public AgentRuntime entry point
│  ├─ loop.py              # native multi-round tool loop
│  ├─ guards.py            # structural repetition/failure/no-progress guards
│  └─ context.py           # planned short-term context management
├─ memory/
│  ├─ runtime.py
│  ├─ graph/
│  ├─ activation/
│  ├─ recall/
│  ├─ extraction/
│  └─ tools.py
└─ app/
   └─ runtime.py
```

현재 구현 흐름은 다음과 같다.

```text
User message / existing history
        ↓
AgentRuntime
        ↓
AgentLoop + AgentGuard
        ↓
OllamaAdapter.chat(messages, registry.native_schemas())
        ↓
Ollama / sLLM
        ├─ tool_calls 없음 → final content → 종료
        │
        └─ native tool_calls[]
                 ↓
          ToolRegistry.invoke(call)
                 ↓
          filesystem / terminal handler
                 ↓
          OS filesystem / shell
                 ↓
          role="tool" result
                 ↓
          guard structural progress check
                 ↓
          다시 Ollama
```

전체 목표에서는 이 native agent loop 앞뒤에 Memory Runtime이 결합된다.

```text
User input
   ↓
Memory Runtime
   ├─ raw utterance 저장
   ├─ 이전 activation 로드
   ├─ 현재 입력 activation 갱신
   └─ local subgraph automatic recall
   ↓
MemoryContext + recent dialogue
   ↓
Agent Runtime / native tool loop / guards
   ↓
Final answer
   ↓
Memory extraction
   ↓
장기적으로 남길 정보만 graph mutation
```

## 실행 방법

Python 3.11 이상과 Ollama를 준비하고 초기 모델로 Ornith를 사용할 경우 다음처럼 설치한다.

```bash
ollama pull ornith-1.5:9b

git clone https://github.com/FoggyCaligo/MAI_MyAI_sllm.git
cd MAI_MyAI_sllm
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Git Bash / Linux / macOS:

```bash
source .venv/bin/activate
pip install -e ".[dev]"
```

주요 예정 환경값은 `.env.example`에 있다.

```env
OLLAMA_HOST=http://127.0.0.1:11434
MAIN_MODEL=ornith-1.5:9b
OLLAMA_THINK=true
AGENT_MAX_ROUNDS=30
AGENT_MAX_IDENTICAL_CALLS=3
AGENT_MAX_IDENTICAL_FAILURES=2
AGENT_MAX_NO_PROGRESS_ROUNDS=2
TOOL_TIMEOUT_SECONDS=60
TERMINAL_TIMEOUT_SECONDS=120
MEMORY_DB_PATH=./data/memory.sqlite3
```

계약 테스트:

```bash
python -m pytest -q
```

현재 local PC tools까지 포함한 Agent Runtime은 Python에서 다음처럼 구성할 수 있다.

```python
import asyncio

from mai.agent import AgentRuntime
from mai.llm import ModelConfig, OllamaAdapter
from mai.tools import ToolRegistry, register_local_pc_tools


async def main():
    registry = ToolRegistry()
    register_local_pc_tools(
        registry,
        cwd=None,                       # None이면 현재 프로세스 cwd
        filesystem_timeout_seconds=60,
        terminal_timeout_seconds=120,
    )

    adapter = OllamaAdapter(ModelConfig(
        model="ornith-1.5:9b",
        host="http://127.0.0.1:11434",
        think=True,
    ))
    agent = AgentRuntime(adapter, registry)

    result = await agent.run_user_message(
        "내 Documents 폴더에서 README.md를 찾아 내용 일부를 확인해줘"
    )
    print(result.content)


asyncio.run(main())
```

`register_local_pc_tools()`는 현재 구현된 8개 filesystem tool과 `terminal_run`을 한 번에 등록한다. 이 도구들은 repository 밖 절대경로 접근을 막지 않으며, 실제 OS 계정의 filesystem/process 권한이 최종 경계다. 관리자 권한이 필요한 작업을 일반 권한으로 실행하면 실제 권한 오류가 발생한다. 현재 완성된 App/UI 진입점은 아직 없으므로 Python에서 runtime을 구성해 사용한다. 다음 도구 계층은 code/document/image/web이며, 메모리 계층은 별도 설계 논의 후 이어서 구현한다.
