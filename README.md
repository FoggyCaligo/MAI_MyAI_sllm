# MAI MyAI sLLM

MAI MyAI sLLM은 **그래프 기반 장기기억을 소형 로컬 언어모델(sLLM)에 부여하고, 그 기억을 Ollama native tool과 로컬 PC 작업에 연결하는 개인 에이전트 런타임**이다. 기억 구조는 MACHI MK4에서 잘 작동했던 `사용자 anchor / 원문 utterance / fact / concept / typed edge / provenance` 방식을 기본으로 유지한다. 다만 관련 기억에 진입하는 방법은 MK4의 문자열·activation 중심 회수 대신 `sqlite-vec`을 사용한다. [`Sentence_Breaker`](https://github.com/FoggyCaligo/Sentence_Breaker)가 나눈 동일 segment는 하나의 Concept Node와 하나의 vector만 가지며, vector hit는 곧바로 답으로 쓰이지 않고 그 Concept에 연결된 Fact·원문 Utterance와 현재 사용자 anchor까지의 최소 경로를 Working Graph에 불러오는 출발점으로만 사용한다. 따라서 모델은 `MAI-프로젝트` 같은 해석된 관계 조각만 보는 대신, 실제로 사용자가 어떤 문장을 말했고 그 문장에서 어떤 fact/concept가 파생됐는지를 직접 확인할 수 있다. 의미 Graph 갱신은 tool-use 루프 중간에 하지 않고 **최종 응답이 확정된 뒤 별도 post-response 단계에서 한 번만 수행**한다. 상세한 Memory v1 계약은 [`MEMORY_V1.md`](MEMORY_V1.md), 전체 개발 계약은 [`WORKING_CONTRACT.md`](WORKING_CONTRACT.md)에 있다.

## 도구

모든 실행 도구는 `ToolRegistry`에 등록되어 Ollama native function schema로 모델에 노출된다. Registry는 이름·설명·Pydantic 입력 계약·handler·timeout만 관리하며 문자열 휴리스틱으로 route를 정하지 않는다. 현재 Ollama adapter, native Tool Registry, Agent Runtime, structural Agent Guard, PC-wide Filesystem/Terminal tools가 구현되어 있다. 파일 도구는 repository 밖 절대경로를 허용하며 MAI 프로세스를 실행한 OS 사용자 계정의 실제 권한을 따른다. `terminal_run`도 동일한 사용자 권한으로 로컬 shell을 실행하고 stdout/stderr/returncode/timeout을 숨기지 않는다. Memory v1에는 `memory_search(node_id)`가 추가되며, 이 도구는 선택한 permanent Node의 정확히 1-hop을 Working Graph에 merge하고 새로 보이는 노드들이 현재 사용자 anchor와 연결되는 최소 경로도 함께 유지한다. 향후 code/document/image/web 도구도 같은 native registry 위에 구현한다.

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
│  ├─ graph/                # anchor/utterance/fact/concept + typed edges
│  ├─ vector/               # VectorIndex boundary + sqlite-vec backend
│  ├─ recall/               # concept vector entry + 1-hop/evidence/anchor paths
│  ├─ extraction/           # post-response user FactExtractor contract
│  └─ tools.py              # user-bound native memory_search
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
raw user evidence 저장 + user account anchor 확인
   │  아직 semantic graph mutation 없음
   ↓
Sentence_Breaker(query) → segment[]
   ↓
sqlite-vec search over Concept Nodes only
   ↓
Concept hit
   ├─ Concept 1-hop → Fact / Utterance / adjacent Concepts
   └─ Concept → current user anchor shortest path
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
       user-anchor path 보강
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
   ├─ 원문 Utterance Node 생성
   ├─ user_anchor ─spoke→ utterance
   ├─ utterance ─mentions→ Sentence_Breaker Concepts
   ├─ 사용자 발화에서 Fact 추출
   ├─ user_anchor ─asserted_fact→ fact
   ├─ utterance ─derived_fact→ fact
   └─ fact ─mentions→ Concepts
   ↓
새 Concept만 sqlite-vec에 1회 index
```

`required=true`는 final 전에 해당 capability가 최소 한 번 성공해야 한다는 뜻이고, `required=false`는 사용 금지가 아니다. Agent는 실행 중 새 정보에 따라 다른 도구를 자유롭게 추가 호출할 수 있다.

Memory의 저장/탐색 역할은 다음처럼 분리한다.

```text
sqlite-vec       = 관련 Concept 위치로 빠르게 점프
Permanent Graph  = 사용자 anchor + 원문 + fact + concept + typed provenance
Working Graph    = 현재 턴에 펼쳐 놓은 기억
memory_search    = 선택한 Node에서 한 hop 더 의도적으로 탐색
```

원문은 Fact와 별도로 유지된다.

```text
User Anchor
   └─spoke→ "나는 MAI를 개인 AI 프로젝트로 만들고 있어."
                  ├─mentions→ MAI
                  └─derived_fact→ "MAI는 사용자의 개인 AI 프로젝트다."
                                      └─mentions→ MAI
```

이 구조에서는 모델이 기억을 요약해 답할 수 있으면서도, 필요할 때 그 기억의 근거가 된 실제 사용자 문장까지 직접 확인할 수 있다.

## 실행 방법

Python 3.11 이상과 로컬 Ollama가 필요하다. 프로젝트를 clone한 뒤 가상환경을 만들고 설치한다. `Sentence_Breaker`와 `sqlite-vec`은 `pyproject.toml` dependency로 함께 설치된다.

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

Ollama를 실행하고 사용할 native-tool 지원 모델과 embedding 모델을 준비한다.

```bash
ollama serve
ollama pull ornith-1.5:9b
```

Embedding은 `EmbeddingProvider` 경계 뒤에서 Ollama `/api/embed`를 사용하도록 구현되어 있으므로 실제 embedding 모델은 runtime 구성에서 별도로 지정할 수 있다. Graph와 sqlite-vec은 같은 `memory.db` 파일을 사용할 수 있지만, Memory core는 `VectorIndex` protocol에만 의존하므로 backend 교체가 가능하다.

환경 설정은 `.env.example`을 기준으로 한다. 현재 repository는 runtime core를 단계적으로 구현 중이므로 완성된 end-user CLI/UI는 아직 없다. 개발 검증은 다음으로 수행한다.

```bash
pytest
```

현재 Memory v1에서 MK4식 사용자 anchor/원문 Utterance/Fact/Concept/typed edge/provenance schema, Sentence_Breaker concept identity, `sqlite-vec` backend, replaceable `VectorIndex`/`EmbeddingProvider` 경계, 사용자 anchor를 포함한 Working Graph recall, one-hop native `memory_search`, post-response fact-write 경계와 frozen required-tool enforcement가 구현되고 있다. 모델 기반 preflight planner와 실제 FactExtractor 연결은 다음 구현 단계이며, 이 경계는 `MEMORY_V1.md`의 저장 의미를 바꾸지 않고 연결하도록 둔다.
