# MAI MyAI sLLM

**사용자를 장기적으로 기억하고, 그 기억을 바탕으로 대화와 로컬 PC 작업을 이어 가는 로컬 sLLM 개인 에이전트 런타임**이다.

이 README는 다른 MACHI/MK 문서를 읽지 않아도 프로젝트의 출발점, 핵심 아이디어, 일반적인 LLM/RAG/Second Brain 계열과의 차이, 전체 구조와 사용 방법을 이해할 수 있도록 작성했다. 세부 메모리 계약은 [`MEMORY_V1.md`](MEMORY_V1.md), 런타임 개발 계약은 [`WORKING_CONTRACT.md`](WORKING_CONTRACT.md)에 분리되어 있다.

---

# 1. 프로젝트 개요

## 1.1 프로젝트 정의

일반적인 대화형 LLM은 현재 대화의 문맥을 이어 가는 데 강하지만, **여러 달·여러 해에 걸쳐 사용자를 기억하고 그 기억이 왜 생겼는지까지 보존하는 일**은 별개의 문제다.

MAI MyAI sLLM은 이 문제를 모델 내부 상태나 특정 서비스 계정의 메모리 기능에 맡기지 않는다. 기억을 사용자가 직접 보유하는 로컬 SQLite 데이터베이스에 저장하고, 기억 사이의 관계를 그래프로 구성한 뒤, 로컬 sLLM이 필요할 때 해당 부분만 꺼내 사용한다.

```text
Local User
   ↓
MAI Runtime
   ├─ Local sLLM / Ollama
   ├─ Native Tool Agent Loop
   ├─ Graph Long-term Memory
   └─ PC Tools
       ├─ Memory
       ├─ Filesystem / Code
       ├─ Document / Image
       ├─ Web Research
       └─ Terminal
```

LLM은 답변 생성, 상황 판단, 도구 선택을 담당한다. 장기기억 자체는 LLM 안에 저장되지 않는다. 따라서 메인 모델을 교체하더라도 사용자 기억의 본체는 그대로 남는다.

## 1.2 프로젝트 시작 계기

이 프로젝트의 출발점은 **"대화 기록이 남는 것"과 "사용자에 대한 이해가 남는 것"은 다르다**는 경험이었다.

이전에 회사 계정으로 사용하던 GPT에 더 이상 접근할 수 없게 되면서, 당시 GPT에게 나를 어떻게 이해하고 있었는지 정리해 달라고 요청해 문서로 옮긴 뒤 새 개인 계정에 다시 제공한 적이 있었다.

하지만 옮겨진 것은 기록과 설명이었다. 새 모델은 그 문서를 다시 읽고, 다시 해석하고, 다시 검증해야 했다. 기대했던 것은 "이해의 이전"이었지만 실제로는 **기억의 이전이 곧 이해 구조의 이전은 아니었다.**

여기서 두 가지 문제가 드러났다.

1. 사용자의 장기 맥락이 특정 계정이나 플랫폼에 묶이면 사용자가 자신의 기억을 직접 소유하기 어렵다.
2. 모델이 무엇을 기억하는 것처럼 보여도, 왜 그렇게 이해했는지와 어떤 원문에서 나온 기억인지 확인하기 어렵다.

MAI는 그래서 기억을 서비스의 부속 기능이 아니라 **사용자가 직접 소유하고, 검사하고, 다른 모델과 함께 계속 사용할 수 있는 독립 데이터 구조**로 둔다.

## 1.3 프로젝트 구성

### Ollama

로컬 모델 실행과 native tool calling을 담당한다. MAI는 모델이 생성한 Ollama `tool_calls`를 별도의 문자열 포맷으로 다시 해석하지 않고 native 구조를 그대로 사용한다.

### Local sLLM

기본 모델 설정은 `ornith-1.5:9b`이다. 모델은 대화 생성, 도구 선택, 검색 결과 해석, 메모리 활용을 담당한다. 특정 모델 전용 JSON 문법이나 문자열 기반 route 규칙에 의존하지 않는다.

### Graph Long-term Memory

사용자 원문, 원문에서 파생된 Fact, 재사용 가능한 Concept, 그리고 이들 사이의 typed relation과 provenance를 SQLite graph로 저장한다.

### Native Tool Agent

그래프가 직접 "생각하는 엔진"이 되는 대신, LLM이 현재 요청을 보고 필요한 도구를 조합한다. 기억은 장기 저장과 회수에 집중하고, 실제 작업 계획은 대화 모델이 맡는다.

프로젝트의 도구 체계는 다음 범주로 구성된다.

| 범주 | 대표 도구 | 역할 |
|---|---|---|
| Memory | `memory_search`, graph search 계열 | 기억 검색, 주변 관계 탐색, 근거 확인 |
| Filesystem | `file_list`, `file_search`, `file_read`, `file_write`, `file_create`, `file_delete`, `file_move`, `file_copy` | PC 파일 탐색·CRUD |
| Code | `code_search`, `code_read`, `code_symbols` | 소스 내용 검색, line read, 구조 탐색 |
| Document | `document_read` | PDF/DOCX 등 문서 내용 추출 |
| Image | `image_analyze` | 이미지 내용과 메타데이터 분석 |
| Web | web search / research 계열 | 최신 외부 정보 검색과 검증 |
| Terminal | `terminal_run` | 로컬 shell command 실행 |
| Tool Manual | `tool_manual` | 필요한 도구의 상세 schema와 사용 계약 조회 |

파일·문서·이미지·터미널 도구는 repository 내부에만 제한되지 않는다. MAI 프로세스를 실행한 OS 사용자 계정이 접근 가능한 범위라면 절대경로를 통해 PC 전체를 다룰 수 있다. 권한 오류, command 실패, timeout은 성공처럼 감추지 않고 그대로 드러낸다.

---

# 2. 프로젝트 핵심 차별점: 그래프 장기기억

## 2.1 정의

MAI의 핵심은 **검색 가능한 대화 로그가 아니라, 근거와 관계를 보존한 장기기억 그래프**다.

기억 하나를 단순한 텍스트 한 줄로 저장하지 않고 다음처럼 분리한다.

```text
User Anchor
   └─spoke→ Utterance
                  ├─mentions→ Concept
                  └─derived_fact→ Fact
                                      └─mentions→ Concept
```

예를 들어 사용자가 이렇게 말했다고 하자.

```text
"나는 MAI를 개인 AI 프로젝트로 만들고 있어."
```

원문은 Utterance로 보존된다. 동시에 `MAI`, `개인 AI 프로젝트` 같은 Concept이 연결되고, 필요한 경우 `MAI는 사용자의 개인 AI 프로젝트다` 같은 Fact가 원문에 연결된다.

모델은 Fact를 이용해 짧고 빠르게 기억을 이해할 수 있고, 필요하면 그 Fact의 근거가 된 실제 사용자 문장까지 다시 확인할 수 있다.

## 2.2 왜 그래프인가

사용자의 장기기억은 독립된 문장들의 모음보다 **서로 연결된 개념의 축적**에 가깝다.

예를 들어 `MAI`라는 Concept은 어느 한 문장에만 존재하지 않는다.

```text
MAI
├─ 사용자가 만드는 개인 AI 프로젝트
├─ Ollama를 사용함
├─ 그래프 장기기억을 사용함
├─ 특정 repository와 연결됨
└─ 여러 시기의 사용자 발화와 연결됨
```

같은 Concept이 반복될 때마다 별개의 기억 덩어리를 만드는 대신 하나의 Concept을 공유하면, 시간이 지날수록 그 Concept 주변에 사용자 고유의 맥락이 쌓인다.

그래프의 목적은 이 관계망을 모델 대신 추론하는 것이 아니다. **필요한 기억을 찾았을 때 그 기억이 어떤 발화·사실·개념과 연결되어 있는지를 구조적으로 함께 보여주는 것**이다.

---

# 3. Vector DB 기반 기억과의 비교

## 3.1 비슷한 점

Vector DB 기반 RAG/memory와 MAI는 다음 점에서 비슷하다.

- 현재 질문과 관련된 과거 정보를 검색한다.
- 전체 장기 데이터를 매번 context에 넣지 않고 일부만 가져온다.
- 장기 저장소와 현재 대화 context를 분리한다.

## 3.2 차이점

일반적인 vector memory는 보통 텍스트 chunk를 embedding vector로 바꾼 뒤 유사도 검색을 한다.

```text
Text Chunk
   ↓ Embedding Model
Dense Vector
   ↓ Similarity Search
Relevant Chunk
```

MAI에서는 검색 index가 기억의 본체가 아니다.

```text
Permanent Graph = 기억의 본체
ConceptIndex    = 기억 그래프로 들어가는 입구
```

ConceptIndex는 embedding을 사용하지 않는다.

```text
Sentence_Breaker Query Segments
   ↓
Exact Hash Lookup
   ↓ miss
SQLite FTS5 Lexical Search
   ↓
Concept Node
   ↓
Fact / Utterance / User Anchor
```

### 특정 embedding 모델에 종속되지 않음

Embedding 기반 retrieval은 vector 자체가 embedding 모델의 좌표계에 종속된다. 모델을 바꾸면 기존 vector index를 다시 계산해야 한다.

MAI의 기본 recall index는 exact text identity와 SQLite FTS5를 사용하므로, 메인 LLM이나 memory-writing LLM을 교체해도 장기기억 검색 좌표계를 재생성할 필요가 없다.

### 반복 개념을 공유할 수 있음

문장 chunk를 독립 저장하는 방식에서는 같은 개념이 수많은 chunk 안에 반복될 수 있다. MAI에서는 동일한 Sentence_Breaker Concept이 하나의 Concept Node를 공유한다.

반복되는 개념이 충분히 많은 장기 개인기억에서는 공통 Concept과 관계를 재사용할 수 있으므로, 문장마다 고차원 embedding을 별도로 저장하는 구조보다 저장량 증가를 완화할 가능성이 있다. 이는 데이터 특성에 따라 달라지는 구조적 장점이지 모든 데이터에서의 절대적인 압축 보장은 아니다.

### 관계를 직접 보관하고 탐색할 수 있음

Vector similarity는 "가깝다"는 신호를 주지만, 왜 관련되는지 자체를 사람이 읽을 수 있는 관계로 보존하지는 않는다.

그래프에서는 다음을 직접 탐색할 수 있다.

```text
Concept
→ 어떤 Fact와 연결되는가
→ 어떤 Utterance에서 나왔는가
→ 누구의 기억인가
→ 어떤 다른 Concept과 같은 근거를 공유하는가
```

---

# 4. Second Brain 계열과의 비교

대표적인 예로 [`NicholasSpisak/second-brain`](https://github.com/NicholasSpisak/second-brain)은 raw article, paper, note, transcript를 LLM이 구조화된 Markdown wiki로 만들고, Obsidian에서 사람이 탐색하는 personal knowledge base다.

## 4.1 비슷한 점

- 개인이 직접 소유하는 장기 지식 저장소를 만든다.
- LLM이 단순 답변 모델이 아니라 저장된 지식을 정리하고 다시 활용하는 역할을 한다.
- 정보 사이의 연결을 유지한다.
- 특정 채팅 한 세션보다 더 긴 시간축을 다룬다.

## 4.2 차이점

Second Brain의 중심 대상은 **사용자가 수집한 지식 자료**이고, 결과물은 사람이 Obsidian에서 탐색하는 Markdown wiki다.

MAI의 중심 대상은 **사용자와 에이전트의 지속적인 상호작용 기억**이며, 그 기억을 대화 모델이 매 턴 직접 사용한다.

```text
Second Brain
Raw Sources → LLM-maintained Wiki → Human Browsing / Query

MAI
Conversation / Tools / Sources
        ↓
Graph Memory
        ↓
Local Agent Recall
        ↓
Conversation + PC Action
```

또한 MAI는 knowledge base만 제공하는 것이 아니라 파일, 코드, 문서, 이미지, 웹, 터미널을 하나의 agent loop에서 사용한다. 기억은 독립 애플리케이션의 결과물이 아니라 **에이전트가 계속 행동하기 위한 지속 상태**다.

## 4.3 MAI 쪽의 강점

- 원문 Utterance와 파생 Fact가 연결되어 있어 기억의 근거를 확인할 수 있다.
- 하나의 Concept에 여러 시기의 발화와 사실이 누적될 수 있다.
- User Anchor를 통해 기억이 누구의 것인지 구조적으로 유지한다.
- 기억 검색과 PC 작업이 같은 native tool loop 안에서 연결된다.
- 기억의 본체가 특정 LLM이나 embedding 모델의 내부 상태에 묶이지 않는다.

---

# 5. 그래프 기억 구현 방법

## 5.1 Node

Node는 장기기억 안에서 직접 참조할 수 있는 정보 단위다.

### User Anchor

```text
identity: anchor:user:<user_id>
type: anchor
payload: { user_id }
```

한 사용자 계정의 기억이 누구에게 속하는지 고정하는 기준점이다.

### Utterance Node

```text
type: utterance
canonical_text: 사용자의 실제 원문
payload:
  user_id
  evidence_id
  speaker
```

사용자가 실제로 말한 문장을 보존한다. 요약 Fact가 잘못되거나 맥락이 필요한 경우 원문까지 돌아갈 수 있다.

### Fact Node

```text
type: fact
canonical_text: 사용자 발화에서 추출된 간결한 사실
payload:
  user_id
```

응답 시 빠르게 활용할 수 있는 의미 단위다. 원문을 대체하지 않고 Utterance에 연결된다.

### Concept Node

```text
type: concept
canonical_text: Sentence_Breaker segment
```

여러 발화와 Fact가 공유할 수 있는 재사용 가능한 개념 단위다. Concept identity는 semantic similarity가 아니라 canonical segment identity로 결정된다.

## 5.2 Edge

Edge는 Node 사이의 의미 관계를 runtime이 정의한 type으로 저장한다.

```text
user_anchor -> utterance : spoke
user_anchor -> fact      : asserted_fact
utterance   -> fact      : derived_fact
utterance   -> concept   : mentions
fact        -> concept   : mentions
```

각 edge에는 `provenance`와 생성 시각이 함께 저장된다.

자유로운 모델 문장을 edge 의미로 저장하는 대신 작은 typed relation vocabulary를 사용하므로, 그래프 구조가 모델의 표현 습관에 따라 매번 달라지지 않는다.

## 5.3 Evidence

사용자 원문은 semantic graph mutation보다 먼저 immutable evidence로 보관한다.

```text
Evidence
  id
  kind
  content
  created_at
```

이후 Utterance Node가 해당 `evidence_id`를 가리킨다.

## 5.4 ConceptIndex: Exact + SQLite FTS5

Concept을 찾는 입구는 두 단계다.

```text
1. Exact lookup
   canonical text → Concept Node ID

2. FTS5 fallback
   lexical query → candidate Concept Node IDs
```

Exact mapping은 SQLite에 영구 저장되고 runtime에서 Python dict로 읽어 hash lookup을 수행한다. FTS5는 lexical retrieval만 담당하며 graph identity를 결정하지 않는다.

기존 graph에 이미 존재하는 Concept Node는 index가 열릴 때 non-destructive하게 동기화된다.

## 5.5 Working Graph

전체 permanent graph를 매번 LLM에 넣지 않는다. 현재 질문과 관련된 작은 subgraph만 현재 턴의 Working Graph에 올린다.

```text
Current Query
   ↓ Sentence_Breaker
ConceptIndex
   ↓
Concept Seed
   ├─ one-hop neighborhood
   └─ shortest path to current User Anchor
   ↓
Working Graph
```

모델이 더 깊은 기억이 필요하면 memory tool로 특정 Node 주변을 추가 탐색한다.

---

# 6. 에이전트 작동 구조

MAI에서 그래프는 생각의 주체가 아니다. **LLM이 계획하고, 그래프와 도구가 그 판단에 필요한 외부 상태를 제공한다.**

기본 실행 계약은 다음과 같다.

```text
User Input
   ↓
Tool Requirement Preflight
   ↓
Raw Evidence + User Anchor
   ↓
Automatic Memory Recall
   ↓
Working Graph
   ↓
Agent Loop
   ├─ Ollama native tool_calls
   ├─ Memory / Files / Code / Document / Image / Web / Terminal
   └─ Tool results → next model round
   ↓
Required Tool Success Check
   ↓
Final Response
   ↓
Post-response Memory Update
```

Tool Requirement Preflight는 현재 요청에서 반드시 성공해야 하는 capability를 먼저 고정한다. Recall 결과가 이미 답을 제공한 것처럼 보여 필요한 외부 도구가 생략되는 일을 막기 위한 실행 계약이다. `required=false`는 해당 tool의 사용 금지가 아니며 agent는 실행 중 필요한 다른 tool을 자유롭게 호출할 수 있다.

Agent loop는 tool 호출을 문자열로 추측하지 않는다. Ollama native schema와 `tool_calls`를 사용하고, registry validation, timeout, 반복 실패 guard를 runtime이 담당한다.

---

# 7. 기억의 저장과 회수

## 7.1 저장

해석된 장기기억은 tool loop 중간에 계속 수정하지 않고 최종 응답 뒤 한 번의 memory update 단계에서 기록한다.

```text
User Utterance
   ↓ immutable Evidence
Agent / Tool Loop
   ↓ Final Response
Memory Writer
   ├─ Utterance Node
   ├─ User Anchor → Utterance
   ├─ Sentence_Breaker Concepts
   ├─ User-grounded Facts
   └─ Typed provenance edges
```

검색이나 도구에서 얻은 세계 정보는 사용자 자신이 말한 Fact와 같은 출처로 기록하지 않는다. 출처가 다른 정보는 출처가 다른 evidence로 구분한다.

## 7.2 자동 회수

현재 입력을 Sentence_Breaker로 나눈 뒤 ConceptIndex를 통해 관련 Concept으로 진입한다. 해당 Concept의 주변 기억과 현재 User Anchor까지의 경로를 작은 Working Graph로 만든다.

## 7.3 의도적 추가 탐색

자동으로 펼쳐진 영역만으로 부족하면 model이 memory tool을 호출해 특정 Node 주변을 더 탐색한다. 한 번의 호출이 graph 전체를 임의 깊이로 숨겨서 순회하지 않고, 호출 단위로 확장 범위를 명확하게 유지한다.

---

# 8. 왜 로컬 개인 에이전트인가

그래프 기억과 local tool access를 같은 runtime에 두면 기억은 단순한 프로필 정보에서 끝나지 않는다.

예를 들어 사용자가 과거에 어떤 repository를 만들고 있다고 말해 두었다면, 이후 agent는 그 기억을 회수한 뒤 실제 PC에서 repository를 찾고, source를 읽고, terminal command를 실행할 수 있다.

```text
Past conversation memory
        ↓
Recall project / preference / decision
        ↓
Find actual local files
        ↓
Inspect code or document
        ↓
Run required command
        ↓
Answer or perform task
```

즉 MAI가 목표로 하는 것은 "나를 기억하는 챗봇"에 그치지 않고, **나를 기억하면서 실제 작업 환경까지 이어서 다룰 수 있는 개인 에이전트**다.

---

# 9. 설치와 사용

## 9.1 Install

Python 3.11 이상과 Ollama가 필요하다.

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

## 9.2 Ollama model

```bash
ollama serve
ollama pull ornith-1.5:9b
```

`.env.example`을 기준으로 모델과 runtime 설정을 구성한다.

Memory recall에는 별도 embedding model이 필요하지 않는다. Python의 SQLite build에 FTS5가 포함되어 있지 않으면 ConceptIndex 초기화가 명시적으로 실패한다.

## 9.3 개발 검증

```bash
pytest
```

## 9.4 C pure-agent 실험

`experiment-pure-agent-c-fts5` 브랜치에는 Tool Requirement Preflight와 automatic recall 없이 모델이 native tools를 스스로 선택하는 C 구조를 독립 실험할 수 있는 harness가 있다.

```bash
python -m mai.experiments.pure_agent_c \
  --user-id test-user \
  "이 프로젝트 README를 직접 읽고 설명해줘."
```

각 실행은 prior dialogue 없이 시작할 수 있고, persistent memory는 유지되므로 과거 사용자 정보를 답하려면 모델이 `memory_recall(query)`를 실제로 호출해야 한다. 상세 실험 방법은 해당 브랜치의 `PURE_AGENT_C_EXPERIMENT.md`에 있다.

---

# 10. 소스 구조

```text
mai/
├─ llm/
│  ├─ models.py
│  └─ ollama.py
├─ agent/
│  ├─ runtime.py
│  ├─ loop.py
│  ├─ guards.py
│  └─ requirements.py
├─ tools/
│  ├─ registry.py
│  ├─ local.py
│  ├─ filesystem.py
│  ├─ code.py
│  └─ terminal.py
├─ memory/
│  ├─ runtime.py
│  ├─ segmenter.py
│  ├─ working.py
│  ├─ graph/
│  ├─ index/
│  ├─ recall/
│  ├─ extraction/
│  └─ tools.py
└─ app/
   └─ runtime.py
```

현재 repository에 직접 들어와 있는 주요 구현은 Ollama native adapter, Tool Registry, multi-round Agent Runtime, structural guard, PC-wide Filesystem/Terminal, Code discovery, evidence/provenance graph memory, Sentence_Breaker Concept identity, Exact + SQLite FTS5 ConceptIndex, Working Graph recall, one-hop memory tool과 post-response memory 경계다.

Document/Image/Web, tool manual, model-backed Tool Requirement Planner와 FactExtractor는 동작 방식과 구조적 계약이 이미 정리된 MAI 구성요소다. 현재 source tree에서는 아직 모두 이식 완료된 상태가 아니므로, 실제 코드 존재 여부는 [`WORKING_CONTRACT.md`](WORKING_CONTRACT.md)의 구현 상태 구분을 기준으로 확인한다.

---

# 11. 설계 원칙 요약

- **사용자 소유 기억**: 장기기억은 특정 계정이나 외부 플랫폼의 숨은 상태가 아니라 local DB에 남는다.
- **Evidence first**: 해석된 Fact와 실제 사용자 원문을 분리하고 서로 연결한다.
- **Relationship preserving**: Concept을 독립 chunk가 아니라 주변 Fact·Utterance·User와 연결된 graph node로 다룬다.
- **Model-independent memory entry**: Exact + SQLite FTS5를 사용해 embedding 모델 좌표계에 기억을 종속시키지 않는다.
- **LLM as orchestrator**: 그래프가 생각을 대신하지 않고 LLM이 native tools와 memory를 조합한다.
- **Native tool contracts**: 문자열 JSON parser나 의미 문자열 heuristic 대신 Ollama native `tool_calls`와 schema를 사용한다.
- **Visible failures**: 파일 부재, schema 위반, 권한 오류, timeout, command failure를 성공 응답으로 감추지 않는다.
- **Local action**: 기억을 회수하는 것에서 끝나지 않고 실제 PC의 파일·코드·문서·이미지·웹·터미널 작업으로 이어진다.

MAI MyAI sLLM의 핵심을 한 문장으로 줄이면 다음과 같다.

> **사용자가 소유하는 관계형 장기기억을 로컬 sLLM의 대화와 실제 PC 작업에 연결하는 개인 에이전트 런타임.**
