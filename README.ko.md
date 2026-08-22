# Mai

[English](README.md) | **한국어**

> README 유지보수 원칙: `README.ko.md`를 내용의 원본으로 관리하고, 변경 후 영어 `README.md`를 같은 구조와 정보량으로 동기화한다.

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

모델에게는 기본적으로 최근 10개 message 정도만 다시 주입한다. 모든 과거 대화를 매번 context에 넣지 않기 때문에 사용량을 제한하면서도 “아까 말한 것” 같은 자연스러운 대화를 이어갈 수 있다.

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

Scratchpad는 current turn이 끝나면 사라진다. 최종 memory mutation에서 선택된 scratchpad만 장기기억의 근거로 사용되며, scratchpad 전체가 자동으로 graph에 복제되지는 않는다.

## 2.3 Semantic graph — 개인 장기기억

장기 기억은 기본적으로 `data/graph.sqlite3`에 node/edge 관계로 저장된다.

```text
node ─relation→ node
```

동일 user 범위에서 exact same canonical node name이 이미 존재하면 기존 node를 재사용한다. 동일 `(subject, relation, object)` 관계가 반복 확인되면 edge를 다시 복제하지 않고 `support_count`를 증가시킨다.

Framework가 문자열이 비슷하다는 이유만으로 임의의 fuzzy merge를 하지는 않는다.

---

# 3. 시간이 지날수록 사용자를 더 이해하는 이유

Mai의 장기기억은 대화 한 번이 끝날 때 사라지지 않는다.

사용자의 PC 환경, 진행 중 프로젝트, 선호, 과거 결정, 반복되는 요구사항 중 장기적으로 의미 있다고 모델이 선택한 관계는 semantic graph에 누적된다.

나중에 관련 질문이 들어오면 Mai는 graph 전체를 모델에게 던지는 대신 필요한 node 주변만 다시 인출한다. 따라서 시간이 지날수록 **사용자의 상황과 배경을 다시 설명해야 하는 횟수가 줄고, 개인 문맥에 맞는 답을 만들 수 있는 기반이 커진다.**

이 기억은 로컬 SQLite에 저장된다.

```text
data/graph.sqlite3
```

이 파일은 장기 semantic memory와 그 기억에 연결된 durable source evidence를 보관한다. 사용자가 직접 복사하고 백업할 수 있으므로, 장기기억을 **사용자 본인이 소유하고 관리할 수 있는 개인 데이터**로 다룰 수 있다.

전체 대화 기록과 세션은 별도의 `data/chat.sqlite3`에 저장된다. 완전한 상태 백업을 원한다면 Mai를 정상 종료한 뒤 `data/` 폴더 전체를 백업하는 편이 가장 단순하다.

---

# 4. 장기기억이 만들어지고 다시 인출되는 과정

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

원문 전체를 자동으로 장기 graph에 쌓지 않는다.

기억을 다시 꺼낼 때도 graph 전체를 주지 않는다.

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

상세 근거가 필요할 때만:

```text
memory_source_summary
  ↓
memory_source_read
  ↓
필요한 raw evidence 구간
```

으로 내려간다.

Confidence는 source kind의 기본 reliability, `support_count`, revision/conflict 횟수, stability 같은 구조적 신호를 압축한 값이다. 문장 내용을 문자열 휴리스틱으로 읽어서 confidence를 정하지 않는다.

---

# 5. 작은 sLLM의 부담을 줄이는 방법

Mai는 작은 로컬 모델이 긴 prompt와 tool schema에 묻히지 않도록 context를 단계적으로 줄인다.

## 5.1 Tool manual lazy loading

처음부터 모든 tool의 전체 JSON schema를 주지 않는다.

```text
tool name + 짧은 summary
```

만 제공하고, 사용법이 필요할 때:

```text
tool_manual(tool_name)
```

을 호출해 해당 tool의 상세 설명과 schema를 연다.

## 5.2 Tool result compaction

실제 runtime event 원본은 유지하지만, 다음 model round에 다시 주입되는 결과는 compact하게 줄인다.

## 5.3 최근 context 제한

기본 model input은 대략 다음으로 제한된다.

- 현재 user message
- 최근 raw chat 10개 message
- 최근 compact tool operation 5개
- 현재 날짜
- 필요한 graph recall
- current-turn compact tool history
- compact tool catalog
- JSON output contract

## 5.4 동일 성공 action 재실행 방지

같은 tool + 동일 JSON arguments가 이미 성공했다면 동일 side effect를 반복 실행하지 않는다.

## 5.5 Web grounding

웹 evidence를 이용한 최종 답변은 실제 evidence와 연결되는지 별도 grounding pass에서 확인한다. Grounding reviewer는 답변을 다시 쓰지 않고 `accept` 또는 `needs_more_evidence`만 결정한다.

---

# 6. 현재 Tool 목록

## Memory / agent built-ins

| 기능 | 설명 |
| --- | --- |
| `node_lookup` | user graph에서 관련 node 후보 찾기 |
| `recall_memory` | candidate node의 실제 graph 관계 인출 |
| `memory_source_summary` | recall한 node/edge의 compact provenance 조회 |
| `memory_source_read` | 선택된 source의 raw evidence 일부 읽기 |
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

세션 working root 바로 아래에 실제로 존재하는 파일은 turn 시작 시 읽기 provenance에 포함된다. 하위 폴더 파일은 여전히 `file_tree`, `file_search`, `code_search` 같은 정상 discovery를 거쳐야 한다.

## Document / image — owner

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

## Web / market — owner + trial

| Tool | 기능 |
| --- | --- |
| `latest_search` | 최신성 중심 public search |
| `web_research` | 검색 → public page read → evidence package |
| `market_snapshot` | 명시적 provider scope 기반 market lookup/snapshot |

---

# 7. Owner와 Trial

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
- 첨부파일 upload 및 해당 첨부의 자동 text/document/image 분석
- host filesystem 탐색/수정, terminal, code tool은 사용 불가
- download link는 사용 불가
- 한 trial ID에는 active persistent session 하나만 허용

Trial upload는 계정별 폴더로 분리된다.

```text
.mai_uploads/
├─ friend/
└─ family/
```

같은 trial ID로 다른 브라우저에서 새로 로그인하면 이전 session은 폐기된다.

---

# 8. 브라우저를 떠나도 작업이 이어지는 방식

채팅은 persistent job으로 실행된다.

```text
/chat
→ persistent chat job 생성
→ worker thread 실행
→ browser는 job ID polling
```

다른 앱을 보거나 페이지를 새로고침해도 서버 작업은 계속된다. 다시 접속하면 완료된 대화는 `/history`, 실행 중 작업은 `/chat/jobs`에서 복원한다.

---

# 9. 처음 설치하기 — Windows 일반 사용자 기준

아래 과정은 개발 환경이 전혀 없는 PC를 기준으로 한다.

## 9.1 Git 설치

Git for Windows를 설치한 뒤 새 PowerShell에서 확인한다.

```powershell
git --version
```

## 9.2 Python 설치

Python 3를 설치한다. 가능하면 설치 과정에서 **Add Python to PATH**를 활성화한다.

```powershell
python --version
pip --version
```

## 9.3 Ollama 설치

Windows용 Ollama를 설치하고 실행한다.

```powershell
ollama --version
```

### 모델 최소 기준

Mai에서 **작동이 확인된 최소 기준 대화 모델은 `gemma4:e4b`**다.

그보다 작은/약한 모델은 단순 문장 생성은 가능하더라도 구조화 JSON 계약, tool 선택, `tool_manual`, 여러 round 문맥 유지, 장기기억 mutation 등이 불안정할 수 있다. 따라서 **실제 사용에는 `gemma4:e4b` 또는 그 이상의 성능을 가진 모델을 권장한다.**

```powershell
ollama pull gemma4:e4b
ollama pull gemma4:12b
```

`gemma4:12b`는 기본 이미지 분석 모델 예시다.

Ollama 서비스가 따로 필요하면:

```powershell
ollama serve
```

를 별도 터미널에서 실행한다.

## 9.4 저장소 clone

```powershell
git clone https://github.com/FoggyCaligo/MAI_MyAI_sllm.git
cd MAI_MyAI_sllm
```

## 9.5 Python 가상환경

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

## 9.6 package 설치

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

개발/pytest까지 사용할 경우:

```powershell
pip install -r requirements-dev.txt
```

## 9.7 `.env` 만들기

```powershell
Copy-Item .env.example .env
```

예:

```dotenv
MAI_OWNER_ID=owner
MAI_ALLOWED_USER_IDS=friend,family
MAI_OLLAMA_MODEL=gemma4:e4b
MAI_OLLAMA_IMAGE_MODEL=gemma4:12b
MAI_HOST=127.0.0.1
MAI_PORT=8000
```

## 9.8 로컬 실행

```powershell
python run_server.py
```

브라우저에서:

```text
http://127.0.0.1:8000/
```

을 연다.

---

# 10. Tailscale로 외부 접속 설정하기 — 선택사항

Mai를 같은 PC에서만 사용할 거라면 이 단계는 필요 없다.

## 10.1 Tailscale 설치와 로그인

Windows용 Tailscale을 설치하고 system tray의 Tailscale 아이콘에서 로그인한다.

설치 후 PowerShell에서 확인한다.

```powershell
tailscale version
tailscale status
```

`tailscale status`에서 현재 기기가 연결된 상태로 보여야 한다.

## 10.2 Serve와 Funnel 차이

- **Tailscale Serve**: 같은 tailnet에 로그인된 기기들끼리만 Mai에 접근한다.
- **Tailscale Funnel**: Mai를 일반 인터넷에서도 접근 가능한 HTTPS 주소로 공개한다.

Mai의 `start_public_tailscale.cmd`는 **Funnel**을 사용한다. Funnel은 외부 인터넷에 공개되는 기능이므로 owner/trial 계정 설정을 신중히 관리한다.

처음 Funnel을 사용할 때 Tailscale이 HTTPS/MagicDNS/Funnel 사용 승인을 위한 안내 URL을 보여줄 수 있다. 화면 안내에 따라 해당 tailnet에서 기능을 활성화한다.

## 10.3 Mai를 Funnel로 실행

프로젝트 폴더에서:

```powershell
.\start_public_tailscale.cmd
```

을 실행한다.

스크립트는:

```text
Tailscale 연결 확인
→ Funnel 구성
→ Funnel 주소 출력
→ 같은 터미널 foreground에서 Mai 실행
```

순으로 동작한다.

이제 이 터미널이 Mai 서버 프로세스를 직접 붙잡고 있으므로 **이 창에서 `Ctrl+C`를 누르면 Mai 서버가 정상 종료된다.**

Funnel 상태는 별도로:

```powershell
tailscale funnel status
```

으로 확인할 수 있다.

Tailnet 내부에서만 쓸 경우에는 Tailscale 공식 `serve` 명령을 사용해 `http://127.0.0.1:8000`을 공유할 수 있다. Serve/Funnel CLI는 Tailscale 버전에 따라 변경될 수 있으므로 문제가 있으면 최신 Tailscale 공식 문서를 확인한다.

---

# 11. 정상 종료

## `python run_server.py`로 실행한 경우

같은 터미널에서:

```text
Ctrl+C
```

를 누른다.

## `start_public_tailscale.cmd`로 실행한 경우

최신 스크립트 역시 Python을 같은 터미널 foreground에서 실행하므로 같은 창에서:

```text
Ctrl+C
```

를 누른다.

정상 종료 흐름은:

```text
Ctrl+C
→ Uvicorn shutdown
→ FastAPI lifespan cleanup
→ SQLite connection close
→ process 종료
```

이다.

### 구버전 launcher 때문에 서버가 이미 백그라운드에 남아 있다면

포트 8000을 점유한 PID를 찾아 한 번 종료한다.

```powershell
$pid = (Get-NetTCPConnection -LocalPort 8000 -State Listen).OwningProcess
Stop-Process -Id $pid
```

그 뒤 최신 스크립트로 다시 실행하면 된다.

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

- semantic graph nodes/edges
- user anchor
- support/conflict 신호
- durable graph source evidence
- graph → source 연결

## `data/chat.sqlite3`

- raw conversation history
- compact recent tool-operation history
- authenticated sessions
- persistent chat jobs

Mai 상태 전체를 보존하려면 정상 종료 후 `data/` 폴더 전체를 백업하는 편이 안전하다.

---

# 13. 개발 테스트

```powershell
python -m pytest -q
```

으로 전체 contract test를 실행한다.

오류를 테스트 통과용 fallback으로 숨기지 않는다. Runtime contract가 바뀌었다면 테스트 fixture도 새 필수 계약을 명시적으로 충족하도록 수정한다.

---

# 14. 문서

- [`README.md`](README.md) — 영어 메인 README
- [`README.ko.md`](README.ko.md) — 한국어 원본 README
- [`CONTRACT.md`](CONTRACT.md) — 핵심 runtime/product 계약
- [`ROADMAP.md`](ROADMAP.md) — 남은 발전 계획
- [`docs/`](docs/) — 세부 운영/계약 문서
