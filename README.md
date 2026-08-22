# Mai

Mai는 **로컬 sLLM에 장기기억과 여러 도구를 붙여, 사용자의 PC 전반을 관리할 수 있게 만드는 semi-GPT 구현 프로젝트**다.

대화 모델 자체를 무작정 크게 만드는 대신, 작은 로컬 모델 바깥에 기억·도구·세션·근거 검증 계층을 붙여 실제 사용성을 높인다.

Mai가 지향하는 사용 경험은 다음과 같다.

- 평범한 대화를 자연스럽게 이어간다.
- 중요한 정보는 여러 대화를 넘어 장기적으로 기억한다.
- 시간이 지날수록 사용자의 상황, 배경, 선호, 진행 중인 일을 더 많이 이해한다.
- 같은 질문이라도 축적된 개인 문맥에 맞춰 더 적절한 답을 할 수 있다.
- 파일, 문서, 이미지, 코드, 터미널, 웹 검색 같은 실제 도구를 사용할 수 있다.
- 기억은 외부 서비스 계정에만 묶이는 것이 아니라 로컬 SQLite DB에 저장되며, 사용자가 직접 백업하고 소유할 수 있다.

즉 Mai는 **“작은 모델 하나에게 모든 정보를 매번 다시 설명하는 방식”이 아니라, 필요한 기억과 도구만 그때그때 짧게 꺼내 쓰게 만드는 개인용 로컬 AI**를 목표로 한다.

---

# 1. 전체 구조

```text
사용자
  ↓
최근 대화 context
  ↓
Mai single agent
  ├─ semantic graph recall
  ├─ scratchpad working memory
  ├─ attachment evidence
  ├─ file / document / image
  ├─ code search
  ├─ terminal
  ├─ web / market
  └─ 기타 work tools
  ↓
최종 답변
  ↓
모델이 선택한 의미만 장기 graph memory에 기록
```

그래프가 직접 사고하는 구조는 아니다. 판단, 도구 선택, 답변 생성은 하나의 대화 LLM이 담당한다. Graph와 각종 tool은 그 LLM이 필요한 정보를 기억하고 실제 작업을 수행할 수 있도록 지원한다.

---

# 2. 기억의 3계층

Mai는 기억을 수명과 역할에 따라 세 층으로 분리한다.

```text
1. Recent chat / turn memory
   → 바로 전 대화의 문맥 유지

2. Scratchpad
   → 현재 작업 중 임시 working memory

3. Semantic graph memory
   → 여러 턴과 재실행을 넘어 유지되는 장기 기억
```

## 2.1 Recent chat — 직전 대화 문맥

최근 user/assistant 대화는 raw text 그대로 `chat.sqlite3`에 저장된다.

모델에게는 기본적으로 최근 10개 message 정도만 다시 주입한다. 모든 과거 대화를 매번 모델 context에 넣지 않기 때문에 context 사용량을 제한하면서도 “아까 말한 것” 같은 자연스러운 대화를 이어갈 수 있다.

Raw chat은 장기 graph와 역할이 다르다. 모든 문장을 자동으로 graph에 복제하지 않는다.

## 2.2 Scratchpad — 현재 작업용 임시 기억

파일, 첨부, 웹 검색, tool 실행 결과처럼 한 작업 중 잠깐 유지해야 하는 정보는 scratchpad에 둘 수 있다.

```text
attachment / tool evidence
        ↓
scratchpad_put
        ↓
scratchpad:1
        ↓
추가 조사 / 작업
        ↓
scratchpad_update
```

Scratchpad는 current turn이 끝나면 사라진다.

중요한 내용만 최종 memory mutation에서 `scratchpad_id`로 선택할 수 있으며, 선택되지 않은 scratchpad 전체가 장기기억으로 복제되지는 않는다.

## 2.3 Semantic graph — 개인 장기기억

장기 기억은 기본적으로 `data/graph.sqlite3`에 node/edge 관계로 저장된다.

```text
node ─relation→ node
```

예:

```text
사용자 ─진행 중 프로젝트→ Mai
사용자 ─선호→ 어떤 작업 방식
Mai ─목적→ 로컬 개인 AI
```

동일 user 범위에서 exact same canonical node name이 이미 존재하면 기존 node를 재사용한다. 동일 `(subject, relation, object)` 관계가 반복 확인되면 edge를 다시 복제하지 않고 `support_count`를 증가시킨다.

Framework가 문자열이 비슷하다는 이유만으로 임의의 fuzzy merge를 하지는 않는다.

---

# 3. 시간이 지날수록 사용자를 더 이해하는 이유

Mai의 장기기억은 대화 한 번이 끝날 때 사라지지 않는다.

예를 들어 여러 날에 걸쳐 사용자가 다음과 같은 정보를 이야기했다고 하자.

```text
사용 중인 PC 환경
진행 중인 프로젝트
좋아하는 작업 방식
이전에 내린 결정
자주 반복되는 요구사항
```

이 중 장기적으로 의미 있다고 모델이 선택한 관계는 semantic graph에 누적된다.

나중에 관련 질문이 들어오면 Mai는 graph 전체를 모델에게 던지는 대신 필요한 node 주변만 다시 인출한다. 따라서 시간이 지날수록 **사용자의 상황과 배경을 다시 설명해야 하는 횟수가 줄고, 개인 문맥에 맞는 답을 만들 수 있는 기반이 커진다.**

이 기억은 모델 provider의 서버에만 존재하는 계정 상태가 아니다. 로컬 SQLite 파일로 유지되므로 사용자가 직접 복사하고 백업할 수 있다.

```text
data/graph.sqlite3
```

이 파일은 Mai의 장기 semantic memory와 그 기억에 연결된 durable source evidence를 보관한다. 백업해 두었다가 같은 Mai 환경에 복원하면 개인 장기기억도 함께 보존할 수 있다.

따라서 장기기억을 **사용자 본인이 소유하고 관리할 수 있는 개인 데이터**로 다루는 것이 가능하다.

전체 대화 기록과 실행 세션은 별도의 `chat.sqlite3`에 저장된다. 완전한 상태 백업을 원한다면 Mai를 정상 종료한 뒤 `data/` 디렉터리 전체를 백업하는 것이 가장 단순하다.

---

# 4. 장기기억이 만들어지는 과정

한 turn은 대략 다음 순서로 진행된다.

```text
현재 user 발화
+ 최근 raw chat
+ 필요 시 기존 graph recall
+ attachment evidence
+ 현재 tool 결과
+ 필요 시 scratchpad
        ↓
      Agent
        ↓
최종 answer + memory mutation 계획
        ↓
Framework가 node / edge / scratchpad / source scope 검증
        ↓
선택된 semantic relation만 graph에 write/revise
        ↓
선택된 source evidence를 graph provenance에 연결
```

중요한 원칙은 **원문 전체를 자동으로 장기 graph에 쌓지 않는 것**이다.

모델이 최종 단계에서 장기적으로 의미 있다고 선택한 relation만 저장한다. Scratchpad를 근거로 한 기억은 해당 scratchpad와 실제 attachment/tool/web evidence까지 source chain으로 연결할 수 있다.

---

# 5. 장기기억을 다시 꺼내는 과정

모델은 `graph.sqlite3` 전체를 매번 받지 않는다.

```text
현재 질문
  ↓
node_lookup
  ↓
실제 candidate node ID
  ↓
recall_memory
  ↓
1-hop semantic relation
+ compact confidence
+ source_kind
```

기본 recall은 작게 유지한다.

상세 근거가 필요할 때만:

```text
memory_source_summary
  ↓
출처 종류 / reliability / stability / support / conflict / source ID
  ↓
필요한 경우에만
memory_source_read
  ↓
실제 raw evidence 일부
```

순으로 내려간다.

즉 tool의 `tool_manual`과 마찬가지로 memory provenance도 **lazy disclosure** 방식이다.

Confidence는 모델이 임의로 만든 감정적 확신도가 아니라 다음과 같은 구조적 신호를 압축한 값이다.

- source kind의 기본 reliability
- 동일 edge가 반복 확인된 `support_count`
- revision/conflict 횟수
- source 종류별 stability

문장 내용을 `if text contains ...` 식으로 읽어서 confidence를 정하지 않는다.

---

# 6. 작은 sLLM의 부담을 줄이는 방법

Mai는 작은 로컬 모델이 긴 prompt/tool schema에 묻히지 않도록 여러 단계에서 context를 줄인다.

## 6.1 Tool 사용법은 필요할 때만 연다

처음부터 모든 tool의 전체 JSON schema를 주지 않는다.

처음에는:

```text
tool name + 짧은 summary
```

만 제공한다.

실제 사용이 필요할 때 모델이:

```text
tool_manual(tool_name)
```

을 호출하면 그 tool의 full description과 argument schema가 활성화된다.

이미 manual을 읽은 tool은 같은 turn에서 다시 manual 대상으로 남기지 않는다.

## 6.2 Tool result compaction

실제 runtime event 원본은 유지하지만 다음 model round에 재주입되는 결과는 compact하게 줄인다.

큰 파일이나 웹 페이지 하나 때문에 남은 대화 context가 전부 밀려나는 문제를 줄이기 위한 구조다.

## 6.3 최근 context만 제한적으로 주입

기본 model input은 대략 다음으로 제한된다.

- 현재 user message
- 최근 raw chat 10개 message
- 최근 compact tool operation 5개
- 현재 날짜
- 필요한 graph recall
- current-turn compact tool history
- compact tool catalog
- JSON output contract

## 6.4 동일 성공 action 재실행 방지

같은 tool + 동일 JSON arguments가 이미 성공했다면 동일 side effect를 반복 실행하지 않는다.

## 6.5 Web grounding

웹 evidence를 이용한 최종 답변은 실제 evidence와 연결되는지 별도 grounding pass에서 확인한다.

Grounding reviewer는 답변을 마음대로 다시 쓰지 않고 `accept` 또는 `needs_more_evidence`만 결정한다.

---

# 7. 현재 Tool 목록

## Memory / agent built-ins

| 기능 | 설명 |
| --- | --- |
| `node_lookup` | user graph에서 관련 node 후보 찾기 |
| `recall_memory` | candidate node의 실제 graph 관계 인출 |
| `memory_source_summary` | recall한 node/edge의 compact provenance 조회 |
| `memory_source_read` | summary에서 확인한 source의 raw evidence를 필요한 구간만 읽기 |
| `tool_manual` | work tool의 상세 설명과 JSON schema 조회 |
| `scratchpad_put` | evidence 기반 turn-local working memory 생성 |
| `scratchpad_update` | 기존 scratchpad 갱신 |
| final memory mutation | 최종 semantic graph write/revise |

## File / workspace — owner

| Tool | 기능 |
| --- | --- |
| `file_tree` | 디렉터리 구조 조회 |
| `file_search` | 파일/path 검색 |
| `file_text_search` | 파일 내용 검색 |
| `file_read` | 일반 텍스트 파일 읽기 |
| `file_create` | 새 파일 생성 |
| `file_update` | 기존 파일 수정 |
| `file_delete` | 파일 삭제 |
| `file_download_link` | 임시 브라우저 다운로드 링크 생성 |

Existing-file mutation은 current turn에서 실제로 established된 path provenance를 요구한다. Owner filesystem 접근의 최종 경계는 실제 OS/filesystem permission이다.

## Document / image — owner work tool

| Tool | 기능 |
| --- | --- |
| `document_read` | PDF / DOCX / TXT / MD / MARKDOWN 읽기 |
| `image_analyze` | `.env`의 독립 vision model로 이미지 분석 |

## Code — owner

| Tool | 기능 |
| --- | --- |
| `code_index` | Python repository 구조를 compact index로 생성 |
| `code_search` | 구조 index에서 파일/symbol 검색 |

## Terminal — owner

| Tool | 기능 |
| --- | --- |
| `terminal_command` | 현재 PC에서 shell command 실행 |

Terminal output encoding은 `.env`의 `MAI_TERMINAL_ENCODING`이 결정한다.

## Web / market — owner + trial

| Tool | 기능 |
| --- | --- |
| `latest_search` | 최신성 중심 public search |
| `web_research` | 검색 → public page read → evidence package |
| `market_snapshot` | 명시적 provider scope 기반 market lookup/snapshot |

---

# 8. Owner와 Trial

Mai의 로그인 ID는 `owner`와 `trial` role로 구분된다.

## Owner

- 전체 work tool 사용
- PC filesystem / code / terminal 접근
- 문서와 이미지 tool 직접 호출
- upload/download
- 여러 persistent session 유지 가능

## Trial

- 독립된 user graph memory
- core memory capability
- web/market tool
- **첨부파일 upload 및 해당 첨부의 자동 text/document/image 분석**
- host filesystem 탐색/수정, terminal, code tool은 사용 불가
- download link는 사용 불가
- 한 trial ID에는 active persistent session 하나만 허용

Trial upload는 계정별 폴더로 분리된다.

```text
.mai_uploads/
├─ friend/
│  └─ ...
└─ family/
   └─ ...
```

Trial은 자기 계정 upload directory 밖의 path를 attachment로 제출할 수 없다. 따라서 “첨부한 파일은 읽을 수 있지만 host PC의 임의 파일을 탐색할 수는 없는” 경계가 유지된다.

같은 trial ID로 다른 브라우저에서 새로 로그인하면 이전 session은 폐기된다.

---

# 9. 브라우저를 떠나도 작업이 이어지는 방식

`POST /chat`은 긴 모델 작업이 끝날 때까지 HTTP request 자체를 붙잡지 않는다.

```text
/chat
→ persistent chat job 생성
→ worker thread 실행
→ browser는 job ID polling
```

다른 앱을 보거나 페이지를 새로고침해도 서버 작업은 계속된다.

다시 접속하면 완료된 대화는 `/history`, 실행 중 작업은 `/chat/jobs`에서 복원한다.

서버 프로세스 자체가 꺼지면 실행 중 job은 성공으로 추측하지 않고 `interrupted`로 남는다.

---

# 10. 처음 설치하기 — Windows 일반 사용자 기준

아래 과정은 개발 환경이 전혀 없는 PC를 기준으로 한다.

## 10.1 Git 설치

Git for Windows를 설치한다.

설치 후 PowerShell을 새로 열고 확인한다.

```powershell
git --version
```

버전이 출력되면 된다.

## 10.2 Python 설치

Python 3를 설치한다. 설치 프로그램에서 가능하면 **Add Python to PATH**를 활성화한다.

설치 후 새 PowerShell에서 확인한다.

```powershell
python --version
pip --version
```

## 10.3 Ollama 설치

Windows용 Ollama를 설치하고 실행한다.

확인:

```powershell
ollama --version
```

기본 모델을 미리 받는다.

```powershell
ollama pull gemma4:e4b
ollama pull gemma4:12b
```

Ollama 서비스가 실행되지 않는 환경에서는:

```powershell
ollama serve
```

를 별도 터미널에서 실행한다.

## 10.4 Mai 저장소 clone

원하는 작업 폴더로 이동한 뒤:

```powershell
git clone https://github.com/FoggyCaligo/MAI_MyAI_sllm.git
cd MAI_MyAI_sllm
```

## 10.5 Python 가상환경 생성

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

PowerShell 정책 때문에 Activate.ps1 실행이 차단되면 정책을 임의로 시스템 전체 변경하기보다, Python venv 사용법에 맞춰 현재 환경에서 허용되는 shell을 사용한다.

## 10.6 Python package 설치

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

개발/pytest까지 사용할 경우:

```powershell
pip install -r requirements-dev.txt
```

## 10.7 `.env` 만들기

```powershell
Copy-Item .env.example .env
```

`.env`를 메모장이나 VS Code로 열어 계정을 설정한다.

예:

```dotenv
MAI_OWNER_ID=owner
MAI_ALLOWED_USER_IDS=friend,family

MAI_OLLAMA_MODEL=gemma4:e4b
MAI_OLLAMA_IMAGE_MODEL=gemma4:12b
MAI_HOST=127.0.0.1
MAI_PORT=8000
```

`MAI_OWNER_ID`는 전체 PC tool을 사용할 수 있는 owner 계정이다.

`MAI_ALLOWED_USER_IDS`에는 쉼표로 여러 trial 계정을 적을 수 있다.

```dotenv
MAI_ALLOWED_USER_IDS=friend,family
```

## 10.8 Mai 실행

프로젝트 폴더와 venv가 활성화된 PowerShell에서:

```powershell
python run_server.py
```

기본 주소는:

```text
http://127.0.0.1:8000/
```

이다. 브라우저에서 열고 `.env`에 설정한 ID로 로그인한다.

## 10.9 외부에서 접속하기 — 선택사항

Tailscale 설정이 되어 있다면:

```powershell
.\start_public_tailscale.cmd
```

를 사용할 수 있다.

외부 공개 설정은 로컬 전용 실행보다 공격 표면이 커지므로 owner ID와 허용 ID를 신중히 관리한다.

---

# 11. 정상 종료

의도된 종료 방법은 Mai를 실행한 터미널에서:

```text
Ctrl+C
```

이다.

```text
Ctrl+C
→ Uvicorn shutdown
→ FastAPI lifespan cleanup
→ SQLite connection close
→ process 종료
```

Windows terminal에서 `Ctrl+C`가 전달되지 않으면 `Ctrl+Break`도 사용할 수 있다.

터미널 창을 바로 닫는 것은 graceful shutdown이 아니므로 권장하지 않는다.

SQLite는 WAL mode를 사용하므로 실행 중 다음 파일들이 보일 수 있다.

```text
graph.sqlite3
graph.sqlite3-wal
graph.sqlite3-shm
chat.sqlite3
chat.sqlite3-wal
chat.sqlite3-shm
```

`-wal`과 `-shm`은 별도 logical database가 아니라 SQLite가 실행 중 사용하는 보조 파일이다. Mai 실행 중에 직접 삭제하지 않는다.

자세한 내용은 [`docs/OPERATIONS.md`](docs/OPERATIONS.md)를 참고한다.

---

# 12. 데이터와 백업

## `data/graph.sqlite3`

개인 장기기억의 중심 파일이다.

- semantic graph nodes
- semantic graph edges
- user anchor
- support/conflict 신호
- durable graph source evidence
- graph → source 연결

장기기억만 별도로 소유·보관하고 싶다면 Mai를 정상 종료한 뒤 이 파일을 백업할 수 있다.

## `data/chat.sqlite3`

- raw conversation history
- compact recent tool-operation history
- authenticated sessions
- persistent chat jobs

Mai 상태 전체를 그대로 보존하려면 정상 종료 후 `data/` 폴더 전체를 백업하는 편이 안전하다.

DB 파일은 clone 후 처음 실행하면서 자동 생성된다. 즉 처음 사용한 시점부터 기억이 로컬 SQLite에 쌓이며, 이후 사용자가 원하는 위치에 복사해 개인 데이터로 보관할 수 있다.

---

# 13. 개발 테스트

개발 의존성을 설치했다면:

```powershell
python -m pytest -q
```

으로 전체 contract test를 실행한다.

오류를 테스트 통과용 fallback으로 숨기지 않는다. Runtime contract가 바뀌었다면 테스트 fixture도 새 필수 계약을 명시적으로 충족하도록 수정한다.

---

# 14. 문서

루트에는 프로젝트를 처음 이해할 때 필요한 문서만 둔다.

- [`README.md`](README.md) — 프로젝트 개요, 기억 구조, tool, 설치/운영
- [`CONTRACT.md`](CONTRACT.md) — 핵심 runtime/product 계약
- [`ROADMAP.md`](ROADMAP.md) — 남은 발전 계획

세부 문서는 [`docs/`](docs/)에 있다.
