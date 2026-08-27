# MAI MyAI sLLM

MAI MyAI sLLM은 **그래프 기반 장기기억을 소형 로컬 언어모델(sLLM)에 부여하고, 그 기억을 로컬 PC 도구 사용과 연결하는 개인 에이전트 런타임**이다. 일반적인 대화 로그나 단순 VectorDB처럼 과거 문장을 통째로 저장하는 대신, 사용자·프로젝트·사실·선호·정정·출처 같은 내용을 노드와 관계로 축적하고, 현재 대화에서 활성화된 개념 주변의 작은 서브그래프만 모델에 다시 보여주는 것을 핵심 아이디어로 삼는다. 이 방식은 반복되는 개념을 하나의 노드로 공유할 수 있어 장기적으로 중복을 줄일 가능성이 있고, 특정 개념이 어떤 발화·사실·출처·정정 관계를 통해 형성되었는지를 사람이 읽을 수 있는 구조로 남길 수 있다는 장점이 있다. 반면 그래프 자체가 사고까지 맡도록 확장하면 응답 파이프라인이 무거워지고 구조·디버깅·유지보수 비용이 크게 늘어나는 문제가 있었기 때문에, MACHI MK4에서 정리한 방향처럼 **그래프는 장기기억과 회수에 집중하고 실제 계획·도구 선택·응답 생성은 LLM이 담당**하게 한다. 한 턴이 시작되면 사용자 발화를 저장하고 현재 입력과 이전 활성 영역을 바탕으로 관련 기억을 자동 recall한 뒤, 이를 첫 모델 호출의 context에 포함한다. 자동 recall만으로 부족한 경우에는 모델이 별도의 memory tool을 호출해 더 깊게 탐색하고, 최종 응답 뒤에는 필요한 장기 정보만 다시 그래프에 반영하는 구조를 목표로 한다. 모델과 도구 사이의 통신은 자체 JSON 응답 규약을 만들지 않고 Ollama native `tools` / `tool_calls`를 사용한다. 세부 실행 계약과 설계 원칙은 [`WORKING_CONTRACT.md`](WORKING_CONTRACT.md)에 분리해 두었다.

## 도구

MAI의 도구는 모두 `ToolRegistry`에 등록되어 Ollama native function schema로 모델에 노출된다. Registry는 도구의 의미를 문자열로 추측하지 않고, 등록된 이름·설명·Pydantic 입력 모델·실행 함수·timeout 같은 구조적 계약만 관리한다. 현재 **Ollama adapter와 native Tool Registry까지 구현된 상태**이며 실제 filesystem/terminal/web/document/image/memory 구현은 다음 단계에서 순차적으로 연결한다. 예정된 파일 도구는 `file_list`, `file_search`, `file_read`, `file_write`, `file_create`, `file_delete`, `file_move`, `file_copy`로, Python의 실제 filesystem API를 사용하고 workspace에 가두지 않아 MAI 프로세스를 실행한 OS 사용자 계정이 접근 가능한 로컬 PC 전체를 대상으로 한다. `terminal_run`은 subprocess 기반으로 명령·cwd·timeout을 받아 stdout/stderr/returncode를 그대로 반환한다. `code` 도구는 소스 구조 탐색과 검색, `document_read`는 PDF/DOCX/XLSX 등의 파서를 통한 문서 내용 추출, `image_read`는 별도로 설정된 vision-capable Ollama 모델을 통한 이미지 해석, `web` 도구는 최신 외부 정보 조회를 담당한다. Memory 도구는 자동 recall과 별도로 `memory_search`, `memory_get_node`, `memory_get_relations`, `memory_expand`, `memory_get_source` 같은 형태로 제공하여 모델이 장기기억을 능동적으로 더 탐색할 수 있게 한다. 도구 실행 실패, 잘못된 인자, unknown tool, 권한 오류, non-zero exit code, timeout은 성공처럼 보이게 변환하지 않고 실제 실패로 남기는 것을 원칙으로 한다.

## 파일 구조와 전체 작동 구조

코드는 역할별 경계를 분리한다. `mai/llm`은 Ollama native message/thinking/tool call을 MAI 내부 타입으로 옮기는 얇은 adapter, `mai/tools`는 native schema와 실제 실행 함수를 연결하는 registry 및 각 도구 구현, `mai/agent`는 앞으로 multi-round tool loop·guard·context 관리를 담당한다. `mai/memory`는 장기 그래프 저장소, activation, automatic recall, extraction, explicit memory tools를 담당하고, `mai/app`은 최종적으로 대화 session과 UI/server 진입점을 묶는다.

```text
mai/
├─ llm/
│  ├─ models.py            # provider-neutral request/response types
│  └─ ollama.py            # Ollama native adapter
├─ tools/
│  ├─ registry.py          # native schema + validation + executable binding
│  ├─ filesystem.py        # planned: PC-wide file operations
│  ├─ terminal.py          # planned: subprocess execution
│  ├─ code.py              # planned: code inspection/search
│  ├─ documents.py         # planned: PDF/DOCX/XLSX extraction
│  ├─ images.py            # planned: vision-model bridge
│  └─ web.py               # planned: external information retrieval
├─ agent/
│  ├─ runtime.py
│  ├─ loop.py
│  ├─ guards.py
│  └─ context.py
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

전체 한 턴은 아래 방향으로 동작하도록 설계한다.

```text
User input
   ↓
Memory Runtime
   ├─ raw utterance 저장
   ├─ 이전 activation 로드
   ├─ 현재 입력으로 activation 갱신
   └─ 관련 local subgraph automatic recall
   ↓
MemoryContext + recent dialogue
   ↓
Agent Runtime
   ↓
Ollama Adapter
   ↓
Ollama / sLLM
   ├─ content
   ├─ thinking
   └─ native tool_calls
          ↓
      Tool Registry
          ↓
      실제 tool 실행
          ↓
      role=tool result
          └──────────────→ 다시 Ollama
   ↓
Final answer
   ↓
Memory extraction
   ↓
장기적으로 남길 정보만 graph mutation
```

Tool Registry 자체의 흐름은 더 단순하다.

```text
Pydantic input model
      ↓ model_json_schema()
Ollama native tool schema
      ↓
Model selects tool_call(name, arguments)
      ↓
ToolRegistry
      ├─ registered tool 확인
      ├─ arguments validation
      ├─ configured timeout 적용
      └─ exact handler 실행
      ↓
raw tool result 또는 실제 exception
```

이 구조 덕분에 Ornith-1.5:9b를 시작점으로 사용하더라도 runtime을 Ornith 전용으로 만들 필요가 없다. 이후 Ollama native tool calling을 정상 지원하는 다른 sLLM으로 모델만 교체해도 Agent, Tool Registry, Memory Runtime은 그대로 유지하는 것이 목표다.

## 실행 방법

현재 저장소는 **Ollama adapter와 native Tool Registry까지 구현된 개발 중 단계**다. 따라서 아직 완성된 채팅 UI나 전체 Agent Runtime을 실행하는 단일 명령은 제공하지 않는다. 지금 실행 가능한 범위는 개발환경 설치, adapter/registry 테스트, 그리고 Python에서 두 계층을 직접 호출하는 수준이다. 전체 앱 실행 명령은 Agent loop와 app runtime이 구현되는 단계에서 추가한다.

먼저 Python 3.11 이상과 Ollama를 설치하고 Ollama 서버를 실행한다. 초기 대상 모델로 Ornith를 사용할 경우 모델을 준비한다.

```bash
ollama pull ornith-1.5:9b
```

저장소를 받은 뒤 가상환경을 만들고 개발 의존성까지 설치한다.

```bash
git clone https://github.com/FoggyCaligo/MAI_MyAI_sllm.git
cd MAI_MyAI_sllm

python -m venv .venv
```

Windows PowerShell에서는:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Git Bash 또는 Linux/macOS 계열 shell에서는:

```bash
source .venv/bin/activate
pip install -e ".[dev]"
```

환경설정은 `.env.example`을 기준으로 준비한다. 현재 주요 항목은 다음과 같다.

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

현재 구현된 계층의 계약 테스트는 다음 명령으로 실행한다.

```bash
python -m pytest -q
```

Ollama adapter를 직접 확인하려면 Python에서 `ModelConfig`, `OllamaAdapter`, `ChatRequest`를 사용한다. Tool Registry는 각 tool의 입력을 Pydantic model로 정의한 후 실제 handler와 함께 등록한다.

```python
import asyncio
from pydantic import BaseModel, ConfigDict

from mai.llm import ChatRequest, ModelConfig, OllamaAdapter
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

    adapter = OllamaAdapter(ModelConfig(model="ornith-1.5:9b", think=True))
    turn = await adapter.chat(ChatRequest(
        messages=[{"role": "user", "content": "echo 도구로 hello를 반환해줘"}],
        tools=registry.native_schemas(),
    ))

    print(turn.thinking)
    print(turn.tool_calls)

    for call in turn.tool_calls:
        print(await registry.invoke(call))


asyncio.run(main())
```

이 예제는 **한 번의 native model call과 tool 실행까지만** 보여준다. 실제 에이전트에서는 assistant message와 `role=tool` 결과를 history에 추가하고 다시 Ollama를 호출하는 loop가 필요하며, 그 부분이 다음 구현 단계인 `Agent Runtime`에 들어간다. 더 세밀한 guard, memory lifecycle, PC 전체 접근 정책, 오류 계약과 구현 순서는 [`WORKING_CONTRACT.md`](WORKING_CONTRACT.md)를 참고한다.
