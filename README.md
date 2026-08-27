# MAI MyAI sLLM

MAI MyAI sLLM은 **그래프 기반 장기기억을 소형 로컬 언어모델(sLLM)에 부여하고, 그 기억을 Ollama native tool과 로컬 PC 작업에 연결하는 개인 에이전트 런타임**이다. 장기기억의 기본 단위는 문장 전체가 아니라 [`Sentence_Breaker`](https://github.com/FoggyCaligo/Sentence_Breaker)가 나눈 재사용 가능한 segment다. 동일 canonical segment는 하나의 Node와 하나의 vector만 가지므로 반복되는 문장 전체를 계속 vector화해 저장하는 방식의 중복을 줄이는 것을 목표로 한다. VectorDB는 관련 기억의 진입 Node를 빠르게 찾고, 방향성 Graph는 그 Node 주변의 관계와 근거를 탐색한다. `A -> B`와 `B -> A`는 각각 하나의 edge만 존재할 수 있고, 각 edge에는 모델이 작성한 관계 설명을 최신순 최대 3개까지 timestamp/evidence와 함께 보존한다. 원본 evidence는 이 큐와 별도로 불변 저장한다. 자동 recall은 vector hit의 1-hop으로 Working Graph를 만들며, 더 깊은 기억은 모델이 `memory_search`를 반복 호출해 한 hop씩 Working Graph를 확장한다. 의미 관계의 영구 Graph 반영은 tool-use와 같은 턴 중간에 수행하지 않고 **최종 응답이 확정된 뒤 별도 post-response 단계에서 한 번만 수행**한다. 상세한 Memory v1 계약은 [`MEMORY_V1.md`](MEMORY_V1.md), 전체 개발 계약은 [`WORKING_CONTRACT.md`](WORKING_CONTRACT.md)에 있다.

## 도구

모든 실행 도구는 `ToolRegistry`에 등록되어 Ollama native function schema로 모델에 노출된다. Registry는 이름·설명·Pydantic 입력 계약·handler·timeout만 관리하며 문자열 휴리스틱으로 route를 정하지 않는다. 현재 Ollama adapter, native Tool Registry, Agent Runtime, structural Agent Guard, PC-wide Filesystem/Terminal tools가 구현되어 있다. 파일 도구는 repository 밖 절대경로를 허용하며 MAI 프로세스를 실행한 OS 사용자 계정의 실제 권한을 따른다. `terminal_run`도 동일한 사용자 권한으로 로컬 shell을 실행하고 stdout/stderr/returncode/timeout을 숨기지 않는다. Memory v1에는 `memory_search(node_id)`가 추가되며, 이 도구는 선택한 permanent Node의 정확히 1-hop을 반환해 현재 Working Graph에 merge한다. 향후 code/document/image/web 도구도 같은 native registry 위에 구현한다.

## 파일 구조와 전체 작동 구조

```text
mai/
├─ llm/
│  ├─ models.py             # provider-neutral model contracts
│  └─ ollama.py             # Ollama native adapter
├─ tools/
│  ├─ registry.py           # native schema + validation + invocation
│  ├─ filesystem.py         # PC-wide filesystem
│  ├─ terminal.py           # local shell
│  └─ ...                   # code/document/image/web
├─ agent/
│  ├─ runtime.py
│  ├─ loop.py               # native multi-round tool loop
│  ├─ guards.py             # repetition/failure/no-progress guards
│  └─ requirements.py       # frozen pre-recall tool obligations
├─ memory/
│  ├─ runtime.py            # memory lifecycle
│  ├─ segmenter.py          # Sentence_Breaker adapter
│  ├─ working.py            # per-turn Working Graph
│  ├─ graph/                # permanent SQLite graph + evidence
│  ├─ vector/               # replaceable vector DB boundary
│  ├─ recall/               # vector entry + 1-hop expansion
│  ├─ extraction/           # post-response relation proposals
│  └─ tools.py              # native memory_search
└─ app/
   └─ runtime.py
```

한 사용자 턴의 목표 순서는 다음과 같다. **Tool Requirement Preflight가 auto-recall보다 먼저**라는 점이 중요한 계약이다. Recall이나 검색 결과를 먼저 보여주면 이미 정보가 충족된 것처럼 보여 `memory_search`/`web_search` 필요 판정이 false로 편향될 수 있기 때문이다.

```text
User input
   ↓
Tool Requirement Preflight
   │  user request + minimum recent dialogue + capability list만 사용
   │  auto-recall / Working Graph / search result / tool result 없음
   ↓
required tools true/false 판정 → FREEZE
   ↓
raw user evidence 저장
   ↓
Sentence_Breaker → segment[]
   ↓
VectorDB search over unique Nodes
   ↓
vector hit + permanent graph 1-hop
   ↓
Initial Working Graph
   ↓
AgentLoop + AgentGuard
   ↓
Ollama native tool_calls
   ├─ filesystem / terminal / web / ...
   └─ memory_search(node)
          ↓
       Permanent Graph 1-hop
          ↓
       Working Graph merge
          ↓
       다음 model round에서 확장된 graph 확인
   ↓
Frozen required tools가 모두 성공했는지 확인
   ↓
Final response
   ↓
Agent/tool loop 종료
   ↓
Post-response Memory Writer 1회
   ↓
relation proposal → runtime timestamp/evidence 부착
   ↓
Permanent Graph commit
```

`required=true`는 final 전에 해당 capability가 최소 한 번 성공해야 한다는 뜻이고, `required=false`는 사용 금지가 아니다. Agent는 실행 중 새 정보에 따라 다른 도구를 자유롭게 추가 호출할 수 있다.

Memory의 저장/탐색 역할은 다음처럼 분리한다.

```text
Vector index   = 관련 기억 위치로 빠르게 점프
Permanent Graph = 장기 관계와 evidence
Working Graph   = 현재 턴에 펼쳐 놓은 기억
memory_search   = 선택한 Node에서 한 hop 더 의도적으로 탐색
```

## 실행 방법

Python 3.11 이상과 로컬 Ollama가 필요하다. 프로젝트를 clone한 뒤 가상환경을 만들고 설치한다. `Sentence_Breaker`는 `pyproject.toml`의 Git dependency로 함께 설치된다.

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

Linux/macOS:

```bash
source .venv/bin/activate
pip install -e ".[dev]"
```

Ollama를 실행하고 사용할 native-tool 지원 모델을 준비한다.

```bash
ollama serve
ollama pull ornith-1.5:9b
```

환경 설정은 `.env.example`을 기준으로 한다. 현재 repository는 runtime core를 단계적으로 구현 중이므로 완성된 end-user CLI/UI는 아직 없다. 개발 검증은 다음으로 수행한다.

```bash
pytest
```

현재 Memory v1에서 SQLite permanent graph, unique Node/edge 계약, 최신 3개 relation observation queue, immutable evidence, Working Graph, Sentence_Breaker adapter, replaceable vector-index boundary, one-hop recall, native `memory_search`, post-response relation proposal 경계와 frozen required-tool enforcement가 구현되고 있다. **구체적인 production VectorDB/embedding backend와 모델 기반 preflight planner/relation extractor 연결은 다음 구현 단계**이며, 이 경계는 `MEMORY_V1.md`의 계약을 바꾸지 않고 교체 가능하도록 둔다.
