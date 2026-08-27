# MAI MyAI sLLM

MAI MyAI sLLM은 **그래프 기반 장기기억을 소형 로컬 언어모델(sLLM)에 부여하고, 그 기억을 로컬 PC 도구 사용과 연결하는 개인 에이전트 런타임**이다. 일반적인 대화 로그나 단순 VectorDB처럼 과거 문장을 통째로 저장하는 대신, 사용자·프로젝트·사실·선호·정정·출처 같은 내용을 노드와 관계로 축적하고 현재 대화에서 활성화된 개념 주변의 작은 서브그래프만 모델에 다시 보여주는 것을 핵심 아이디어로 삼는다. 반복되는 개념을 하나의 노드로 공유할 수 있어 장기적으로 중복을 줄일 가능성이 있고, 특정 개념이 어떤 발화·사실·출처·정정 관계를 통해 형성되었는지를 사람이 읽을 수 있는 구조로 남길 수 있다는 장점이 있다. 반면 그래프 자체가 사고까지 맡도록 확장하면 응답 파이프라인이 무거워지고 구조·디버깅·유지보수 비용이 커질 수 있으므로, MACHI MK4에서 정리한 방향처럼 **그래프는 장기기억과 회수에 집중하고 실제 계획·도구 선택·응답 생성은 LLM이 담당**하게 한다. 한 턴이 시작되면 사용자 발화를 저장하고 현재 입력과 이전 활성 영역을 바탕으로 관련 기억을 자동 recall한 뒤 첫 모델 호출 context에 포함하고, 자동 recall만으로 부족한 경우에는 모델이 memory tool을 호출해 더 깊게 탐색하며, 최종 응답 뒤에는 필요한 장기 정보만 그래프에 반영하는 구조를 목표로 한다. 모델과 도구 사이의 통신은 자체 JSON 응답 규약 대신 Ollama native `tools` / `tool_calls`를 사용한다. 세부 실행 계약과 개발 규칙은 [`WORKING_CONTRACT.md`](WORKING_CONTRACT.md)에 분리되어 있다.

## 도구

모든 도구는 `ToolRegistry`에 등록되어 Ollama native function schema로 모델에 노출된다. Registry는 등록된 이름·설명·Pydantic 입력 모델·실행 함수·timeout 같은 구조적 계약만 관리하며 사용자 문장을 문자열 규칙으로 해석해 도구를 선택하지 않는다. 현재 **Ollama adapter, native Tool Registry, 최소 Agent Runtime까지 구현**되어 실제 native tool-call 왕복을 수행할 수 있다. 이후 연결할 파일 도구는 `file_list`, `file_search`, `file_read`, `file_write`, `file_create`, `file_delete`, `file_move`, `file_copy`이며 Python filesystem API를 사용해 MAI를 실행한 OS 사용자 계정이 접근 가능한 로컬 PC 전체를 대상으로 한다. `terminal_run`은 subprocess 기반으로 command·cwd·timeout을 받아 stdout/stderr/returncode를 보존하고, `code` 도구는 소스 구조 탐색과 검색, `document_read`는 PDF/DOCX/XLSX 파싱, `image_read`는 별도 vision-capable Ollama 모델을 통한 이미지 해석, `web` 도구는 외부 최신 정보 조회를 담당한다. Memory 도구는 automatic recall과 별개로 `memory_search`, `memory_get_node`, `memory_get_relations`, `memory_expand`, `memory_get_source` 형태로 제공할 예정이다. Tool handler가 실패하면 실패를 성공으로 바꾸지 않고 오류 종류와 메시지를 `role=tool` 결과로 모델에 되돌려 다음 라운드에서 복구할 기회를 주며, runtime 기록에도 실패 상태가 남는다.

## 파일 구조와 전체 작동 구조

`mai/llm`은 Ollama native message/thinking/tool call을 MAI 내부 타입으로 옮기는 얇은 adapter, `mai/tools`는 native schema와 실제 실행 함수를 연결하는 registry 및 도구 구현, `mai/agent`는 multi-round tool loop와 이후 guard/context 관리를 담당한다. `mai/memory`는 장기 그래프 저장소, activation, automatic recall, extraction, explicit memory tools를 담당하고, `mai/app`은 최종적으로 대화 session과 UI/server 진입점을 묶는다.

```text
mai/
├─ llm/
│  ├─ models.py            # provider-neutral request/response types
│  └─ ollama.py            # Ollama native adapter
├─ tools/
│  ├─ registry.py          # native schema + strict validation + execution
│  ├─ filesystem.py        # planned: PC-wide file operations
│  ├─ terminal.py          # planned: subprocess execution
│  ├─ code.py              # planned: code inspection/search
│  ├─ documents.py         # planned: PDF/DOCX/XLSX extraction
│  ├─ images.py            # planned: vision-model bridge
│  └─ web.py               # planned: external information retrieval
├─ agent/
│  ├─ runtime.py           # public AgentRuntime entry point
│  ├─ loop.py              # implemented native multi-round tool loop
│  ├─ guards.py            # next: repetition/no-progress guards
│  └─ context.py           # next: short-term context management
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

현재 구현된 Agent loop는 다음처럼 동작한다.

```text
User message / existing history
        ↓
AgentRuntime
        ↓
AgentLoop
        ↓
OllamaAdapter.chat(messages, registry.native_schemas())
        ↓
Ollama / sLLM
        ├─ tool_calls 없음 → final content 반환 → 종료
        │
        └─ native tool_calls[]
                 ↓
          assistant_message를 history에 보존
                 ↓
          ToolRegistry.invoke(call)
                 ↓
          실제 handler 실행
                 ↓
          role="tool", tool_name=..., content=...
          를 history에 추가
                 ↓
          다시 OllamaAdapter.chat(...)
                 └────────────── 반복
```

Ollama가 한 응답에서 여러 tool call을 반환하면 모두 원래 순서대로 실행하고 각각의 `role=tool` 메시지를 추가한다. 현재 최소 runtime에는 전체 작업이 끝없이 지속되는 것을 막기 위한 구조적 `max_rounds` 상한만 들어가 있고, 동일 tool+arguments 반복 감지와 no-progress 판단 같은 세밀한 guard는 다음 단계에서 `mai/agent/guards.py`에 추가한다.

전체 목표 구조에서는 이 Agent loop 앞뒤로 Memory Runtime이 결합된다.

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
Agent Runtime / native tool loop
   ↓
Final answer
   ↓
Memory extraction
   ↓
장기적으로 남길 정보만 graph mutation
```

이 경계 덕분에 Ornith-1.5:9b를 시작점으로 사용하더라도 runtime을 Ornith 전용으로 만들 필요가 없다. 이후 Ollama native tool calling을 정상 지원하는 다른 sLLM으로 모델을 바꿔도 Agent, Tool Registry, Memory Runtime은 그대로 유지하는 것이 목표다.

## 실행 방법

Python 3.11 이상과 Ollama를 설치하고 Ollama 서버를 실행한다. 초기 대상 모델로 Ornith를 사용할 경우 다음처럼 준비한다.

```bash
ollama pull ornith-1.5:9b
```

저장소를 받은 뒤 가상환경을 만들고 개발 의존성까지 설치한다.

```bash
git clone https://github.com/FoggyCaligo/MAI_MyAI_sllm.git
cd MAI_MyAI_sllm
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Git Bash / Linux / macOS shell:

```bash
source .venv/bin/activate
pip install -e ".[dev]"
```

주요 환경설정의 예정 기본값은 `.env.example`에 있다.

```env
OLLAMA_HOST=http://127.0.0.1:11434
MAIN_MODEL=ornith-1.5:9b
MEMORY_MODEL=ornith-1.5:9b
VISION_MODEL=
OLLAMA_THINK=true
AGENT_MAX_ROUNDS=30
AGENT_MAX_IDENTICAL_CALLS=3
TOOL_TIMEOUT_SECONDS=60
TERMINAL_TIMEOUT_SECONDS=120
MEMORY_DB_PATH=./data/memory.sqlite3
```

현재 계약 테스트는 다음으로 실행한다.

```bash
python -m pytest -q
```

최소 Agent Runtime은 Python에서 직접 실행할 수 있다. 아래 예제는 실제로 모델이 `echo` native tool을 선택하면 MAI가 실행 결과를 `role=tool`로 돌려주고, 모델이 최종 답변을 만들 때까지 반복한다.

```python
import asyncio
from pydantic import BaseModel, ConfigDict

from mai.agent import AgentRuntime
from mai.llm import ModelConfig, OllamaAdapter
from mai.tools import ToolRegistry


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


def echo(text: str):
    return {"text": text}


async def main():
    registry = ToolRegistry()
    registry.add(
        name="echo",
        description="Return the supplied text.",
        input_model=EchoInput,
        handler=echo,
    )

    adapter = OllamaAdapter(ModelConfig(
        model="ornith-1.5:9b",
        host="http://127.0.0.1:11434",
        think=True,
    ))
    agent = AgentRuntime(adapter, registry, max_rounds=30)

    result = await agent.run_user_message("echo 도구로 hello를 확인한 뒤 결과를 알려줘")
    print(result.content)
    print(result.tool_executions)


asyncio.run(main())
```

현재는 실제 filesystem/terminal/document/image/web/memory handler와 완성된 App/UI Runtime이 아직 없으므로 위처럼 Python에서 registry를 구성해 실행한다. 다음 구현 단계는 **Agent Guards / Error semantics 강화**이며, 이후 filesystem + terminal 도구를 연결하면 로컬 PC에서 실제 작업을 수행하는 에이전트 형태가 된다. 더 세밀한 설계 계약과 구현 순서는 [`WORKING_CONTRACT.md`](WORKING_CONTRACT.md)를 참고한다.
