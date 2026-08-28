# MAI MyAI sLLM

MAI MyAI sLLM은 **그래프 기반 장기기억을 소형 로컬 언어모델(sLLM)에 부여하고, 그 기억을 Ollama native tool과 로컬 PC 작업에 연결하는 개인 에이전트 런타임**이다. 기억 구조는 MACHI MK4에서 잘 작동했던 `사용자 anchor / 원문 utterance / fact / concept / typed edge / provenance` 방식을 기본으로 유지한다.

관련 기억의 최초 진입은 특정 embedding 모델의 vector 공간에 의존하지 않는다. [`Sentence_Breaker`](https://github.com/FoggyCaligo/Sentence_Breaker)가 만든 Concept segment를 **Exact hash lookup + SQLite FTS5 lexical search**로 찾고, 그 Concept에 연결된 Fact·원문 Utterance·현재 사용자 anchor까지의 최소 경로를 Working Graph에 불러온다. 따라서 메인 LLM이나 memory-writing LLM을 교체해도 기억 검색 좌표계를 다시 만들 필요가 없다. 상세 Memory v1 계약은 [`MEMORY_V1.md`](MEMORY_V1.md), 전체 개발 계약은 [`WORKING_CONTRACT.md`](WORKING_CONTRACT.md)에 있다.

## 도구

모든 실행 도구는 `ToolRegistry`에 등록되어 Ollama native function schema로 모델에 노출된다. Registry는 이름·설명·Pydantic 입력 계약·handler·timeout만 관리하며 문자열 휴리스틱으로 route를 정하지 않는다.

현재 구현된 로컬 도구는 다음과 같다.

- Filesystem: `file_list`, `file_search`, `file_read`, `file_write`, `file_create`, `file_delete`, `file_move`, `file_copy`
- Code discovery: `code_search`, `code_read`, `code_symbols`
- Terminal: `terminal_run`
- Memory: `memory_search(node_id)` — 선택한 permanent Node를 정확히 1-hop 확장하고 사용자 anchor 경로를 유지

`code_search`는 literal/regex, case sensitivity, include/exclude glob, encoding, 파일 크기 제한을 호출자가 명시한다. `code_symbols`는 현재 Python AST만 지원하며 다른 언어의 symbol을 문자열 패턴으로 추측하지 않는다. Document/Image/Web 도구는 아직 구현 대상이다.

## 파일 구조

```text
mai/
├─ llm/
│  ├─ models.py
│  └─ ollama.py
├─ tools/
│  ├─ registry.py
│  ├─ local.py
│  ├─ filesystem.py
│  ├─ code.py
│  ├─ terminal.py
│  ├─ documents.py          # planned
│  ├─ images.py             # planned
│  └─ web.py                # planned
├─ agent/
│  ├─ runtime.py
│  ├─ loop.py
│  ├─ guards.py
│  └─ requirements.py
├─ memory/
│  ├─ runtime.py
│  ├─ segmenter.py
│  ├─ working.py
│  ├─ graph/                # anchor/utterance/fact/concept + typed provenance
│  ├─ index/                # ConceptIndex + exact hash / SQLite FTS5
│  ├─ recall/
│  ├─ extraction/
│  └─ tools.py
└─ app/
   └─ runtime.py
```

## Memory v1 흐름

한 사용자 턴의 목표 순서는 다음과 같다. **Tool Requirement Preflight가 auto-recall보다 먼저**라는 계약은 유지한다.

```text
User input
   ↓
Tool Requirement Preflight
   │  user request + minimum recent dialogue + capability list만 사용
   │  recall / Working Graph / search result / tool result 없음
   ↓
required tools true/false 판정 → FREEZE
   ↓
raw user evidence 저장 + user account anchor 확인
   ↓
Sentence_Breaker(query) → segment[]
   ↓
ConceptIndex
   ├─ exact hash lookup
   └─ miss 시 SQLite FTS5 lexical search
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
   ├─ file/code/terminal/future document/image/web tools
   └─ memory_search(node)
          ↓
       Permanent Graph 1-hop + user-anchor path
   ↓
Frozen required tools 성공 확인
   ↓
Final response
   ↓
Post-response Memory Writer 1회
   ├─ 원문 Utterance Node
   ├─ user_anchor ─spoke→ utterance
   ├─ utterance ─mentions→ Concepts
   ├─ 사용자 발화에서 Fact 추출
   ├─ user_anchor ─asserted_fact→ fact
   ├─ utterance ─derived_fact→ fact
   └─ fact ─mentions→ Concepts
```

새로 생성된 Concept만 `ConceptIndex`에 추가한다. 기존 Memory v1 DB를 열 때 `SqliteFtsConceptIndex`는 graph에 이미 존재하는 Concept Node를 exact/FTS 테이블로 비파괴 동기화한다. 개발 중 예전 sqlite-vec 테이블이 DB에 남아 있더라도 자동 삭제하지 않고 무시한다.

## 왜 Exact + FTS5인가

영구 기억의 검색 가능 여부를 특정 embedding model에 묶지 않기 위해 vector index를 기본 구조에서 제거했다.

```text
Persistent memory
  ├─ Permanent Graph          # 기억 본체
  └─ ConceptIndex
       ├─ exact hash          # canonical Concept의 정확한 조회
       └─ SQLite FTS5         # lexical fallback
```

Concept identity는 Sentence_Breaker의 canonical segment 그대로이며 검색 유사도로 합쳐지지 않는다. Exact mapping은 SQLite에 보존되고 runtime에서 Python dict로 읽어 hash lookup을 수행한다. FTS5는 embedding similarity가 아니라 lexical matching만 담당한다.

이 구조에서는 Ollama 메인 모델을 바꾸거나 향후 memory extraction 모델을 바꾸더라도 long-term memory index를 모델 좌표계 때문에 재생성할 필요가 없다.

## 원문 근거 접근

원문은 Fact와 별도로 유지된다.

```text
User Anchor
   └─spoke→ "나는 MAI를 개인 AI 프로젝트로 만들고 있어."
                  ├─mentions→ MAI
                  └─derived_fact→ "MAI는 사용자의 개인 AI 프로젝트다."
                                      └─mentions→ MAI
```

모델은 Fact로 간결하게 기억을 읽을 수 있고 필요하면 그 Fact의 근거가 된 실제 사용자 문장까지 직접 확인할 수 있다.

## 실행 방법

Python 3.11 이상과 로컬 Ollama가 필요하다.

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

Ollama를 실행하고 native-tool 지원 모델을 준비한다.

```bash
ollama serve
ollama pull ornith-1.5:9b
```

Memory recall에는 별도 embedding model이 필요하지 않는다. SQLite FTS5가 Python의 SQLite build에 포함되어 있어야 하며, 지원되지 않으면 초기화 단계에서 명시적으로 실패한다.

개발 검증:

```bash
pytest
```

현재 Memory v1에는 MK4식 사용자 anchor/원문 Utterance/Fact/Concept/typed edge/provenance, Sentence_Breaker concept identity, model-independent `ConceptIndex`, exact hash + SQLite FTS5 backend, 사용자 anchor를 포함한 Working Graph recall, one-hop native `memory_search`, post-response fact-write 경계와 frozen required-tool enforcement가 구현되고 있다. Tool 계층은 Filesystem/Terminal/Code discovery까지 실제 구현되어 있으며 Document/Image/Web와 모델 기반 preflight planner/FactExtractor 연결은 다음 구현 단계다.
