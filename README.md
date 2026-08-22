# Mai

Mai는 **로컬 sLLM에 장기 기억과 도구 사용 능력을 붙여, 사용자의 PC 전반을 관리할 수 있게 만드는 semi-GPT 구현 프로젝트**다.

기본 언어 모델은 Ollama에서 실행되는 작은 로컬 모델을 사용한다. 모델 자체를 크게 만들기보다, 모델 바깥에 다음 기능을 붙여 실제 사용성을 확보하는 것이 목표다.

- 최근 대화 문맥 유지
- 장기 semantic graph memory
- 현재 작업용 scratchpad
- 파일/문서/이미지 처리
- 코드 탐색
- 터미널 실행
- 웹 조사와 최신 정보 검색
- 시장 정보 조회
- 사용자별 권한과 persistent session
- 브라우저가 잠시 끊겨도 계속 실행되는 chat job

즉 Mai의 핵심 방향은 **“작은 모델 하나에 모든 것을 기억시키는 것”이 아니라, 작은 모델이 필요한 기억과 도구를 짧고 구조화된 형태로 꺼내 쓰게 만드는 것**이다.

---

## 1. 전체 구조

```text
사용자
  ↓
최근 대화 context
  ↓
Mai single agent
  ├─ semantic graph recall
  ├─ scratchpad
  ├─ file / document / image
  ├─ code search
  ├─ terminal
  ├─ web / market
  └─ 기타 work tools
  ↓
최종 답변
  ↓
선택된 의미만 장기 graph memory에 반영
```

그래프가 직접 사고하지 않는다. 실제 판단, 도구 선택, 답변 생성은 하나의 대화 LLM이 담당하고, 그래프와 각종 도구는 그 LLM을 지원한다.

---

# 2. 기억의 3계층

Mai는 기억을 수명과 목적에 따라 세 층으로 분리한다.

```text
1. Turn memory / recent chat
   → 바로 전 대화의 문맥 유지

2. Scratchpad
   → 현재 작업 중 임시로 들고 있는 정보

3. Semantic graph memory
   → 여러 턴을 넘어 유지되는 장기 의미 기억
```

## 2.1 최근 대화 — 문맥 유지용 turn memory

최근 user/assistant 대화는 raw text 그대로 `chat.sqlite3`에 저장된다.

모델에게는 기본적으로 최근 **10개 message**만 다시 주입한다. 사용자/assistant를 한 쌍으로 보면 약 5턴 정도다.

이 계층의 목적은 다음과 같다.

- “아까 말한 것” 같은 직전 대화 연결
- 말투와 문장 맥락 보존
- 모든 대화를 graph로 변환할 필요 제거

최근 대화는 장기 기억과 별개다. 오래된 사실을 기억해야 할 때는 semantic graph를 사용한다.

## 2.2 Scratchpad — 현재 작업용 working memory

Scratchpad는 한 작업을 수행하는 동안 모델이 임시로 사용할 수 있는 메모다.

예를 들어 파일, 웹 검색, 문서 분석 결과에서 필요한 부분만 뽑아:

```text
attachment/tool evidence
        ↓
scratchpad_put
        ↓
scratchpad:1
        ↓
추가 작업
        ↓
scratchpad_update
```

처럼 유지할 수 있다.

Scratchpad는 현재 turn이 끝나면 사라진다. Scratchpad 전체가 자동으로 장기 graph에 저장되지는 않는다.

최종 memory mutation에서 모델이 특정 `scratchpad_id`를 근거로 직접 선택한 경우에만 그 내용이 해당 장기 기억의 근거로 사용된다.

## 2.3 Semantic graph — 장기 기억

장기 기억은 `graph.sqlite3`의 node/edge 구조로 저장한다.

기본 형태는 다음과 같다.

```text
node ─relation→ node
```

예:

```text
사용자 ─이름→ 신재용
사용자 ─진행 중 프로젝트→ Mai
Mai ─목적→ 로컬 sLLM 확장
```

동일 user 안에서 **exact same canonical node name**이 이미 존재하면 새 node를 만들지 않고 기존 node를 재사용한다.

동일한:

```text
(subject, relation, object)
```

edge가 다시 확인되면 edge를 복제하지 않고 `support_count`를 증가시킨다.

의미가 비슷해 보인다는 이유만으로 Framework가 fuzzy/string heuristic으로 node를 임의 병합하지는 않는다.

---

# 3. 장기 기억이 만들어지는 과정

한 turn에서 장기 기억은 대략 다음 순서로 만들어진다.

```text
현재 사용자 발화
+ 최근 대화
+ 필요 시 graph recall
+ 현재 tool 결과
+ attachment evidence
+ scratchpad
        ↓
      Agent
        ↓
최종 answer 확정
        ↓
모델이 필요한 semantic relation만 선택
        ↓
Framework가 현재 turn scope / node / scratchpad ID 검증
        ↓
graph node/edge write 또는 reinforce
```

중요한 점은 **raw 대화, 파일 내용, 웹 결과 전체를 자동으로 graph에 복사하지 않는 것**이다.

Graph에는 모델이 최종 단계에서 장기적으로 의미가 있다고 선택한 semantic relation만 기록된다.

현재 provenance에는 해당 turn의 user/assistant text와 선택된 scratchpad evidence가 연결된다. Graph에서 stable raw source까지 역추적하고, 출처별 confidence를 compact하게 제공하는 구조는 `ROADMAP.md`의 다음 단계에서 확장한다.

---

# 4. 장기 기억을 다시 꺼내는 과정

모델은 모든 graph를 매번 받지 않는다.

필요할 때 다음 구조를 사용한다.

```text
현재 질문
  ↓
node_lookup
  ↓
실제 존재하는 candidate node ID
  ↓
recall_memory
  ↓
해당 node의 1-hop 관계 + user anchor 방향 구조
  ↓
모델 답변
```

즉 graph DB 전체를 context에 집어넣지 않고, **현재 질문에 필요한 작은 부분만 lazy하게 연다.**

향후에는 기본 recall을 `relation + confidence + source_kind` 정도로 더 압축하고, 상세 provenance와 raw source는 필요할 때만 추가 조회하는 구조를 목표로 한다.

---

# 5. 작은 sLLM의 부담을 줄이는 방법

Mai는 작은 로컬 모델이 긴 prompt와 거대한 tool schema 때문에 문맥을 놓치지 않도록 여러 계층에서 입력을 줄인다.

## 5.1 Tool schema를 처음부터 전부 주지 않는다

처음에는 각 tool의:

```text
이름 + 짧은 설명
```

만 compact catalog로 제공한다.

모델이 실제 사용법이 필요하면:

```text
tool_manual(tool_name)
```

을 호출한다.

그 다음 round부터 해당 tool의 상세 description과 JSON input schema가 노출된다.

```text
처음
file_search — 파일 경로 검색
file_read   — 텍스트 파일 읽기
...

필요할 때
 tool_manual("file_read")
        ↓
 file_read full schema
```

이미 manual을 확인한 tool은 다시 manual 대상으로 남기지 않는다.

## 5.2 Tool result도 모델용으로 압축한다

실제 runtime event 원본은 유지하지만, 다음 모델 round에 다시 넣는 tool result는 크기를 제한한다.

즉 디버깅/실행 기록은 잃지 않으면서 모델 context만 줄인다.

## 5.3 최근 정보만 유지한다

기본 model context에는:

- 현재 user message
- 최근 raw chat 10개 message
- 최근 compact tool operation 5개
- 현재 날짜
- 필요한 graph recall
- 현재 turn의 compact tool history
- compact tool catalog
- JSON output contract

정도만 들어간다.

## 5.4 같은 성공 action을 반복 실행하지 않는다

같은 tool과 정확히 같은 JSON arguments로 이미 성공한 action을 다시 실행하려 하면 Framework가 재실행 전에 차단한다.

## 5.5 웹 답변은 evidence grounding을 거친다

`latest_search`나 `web_research`를 사용한 답변은 실제 evidence ID와 연결되는지 grounding review를 거친다.

Grounding 단계는 답변을 마음대로 다시 쓰지 않고:

```text
accept
또는
needs_more_evidence
```

만 결정한다.

---

# 6. Tool 목록

Tool은 role에 따라 노출 범위가 다르다.

## Memory / agent built-ins

| 기능 | 설명 |
| --- | --- |
| `node_lookup` | 현재 user graph에서 관련 node 후보 검색 |
| `recall_memory` | 선택된 node의 실제 graph 관계 회수 |
| `tool_manual` | 특정 work tool의 상세 설명과 JSON schema 조회 |
| `scratchpad_put` | 현재 evidence를 근거로 turn-local working memory 생성 |
| `scratchpad_update` | 기존 current-turn scratchpad 갱신 |
| final memory mutation | 최종 답변 뒤 semantic graph write/revise 실행 |

## File / workspace — owner

| Tool | 기능 |
| --- | --- |
| `file_tree` | 디렉터리 구조 조회 |
| `file_search` | 파일명/path pattern으로 파일 탐색 |
| `file_text_search` | 파일 내용에서 텍스트 검색 |
| `file_read` | 일반 텍스트 파일 읽기 |
| `file_create` | 새 파일 생성 |
| `file_update` | 기존 파일 수정 |
| `file_delete` | 파일 삭제 |
| `file_download_link` | 브라우저에서 받을 수 있는 임시 다운로드 링크 생성 |

Existing-file 작업은 현재 turn에서 실제로 발견된 concrete path provenance를 요구한다. Owner의 filesystem 접근은 앱이 임의 sandbox를 만드는 대신 실제 OS/filesystem permission을 최종 경계로 사용한다.

## Document / image — owner

| Tool | 기능 |
| --- | --- |
| `document_read` | PDF, DOCX, TXT, MD, MARKDOWN 읽기 |
| `image_analyze` | 별도로 설정된 vision model로 이미지 분석 |

첨부된 지원 파일은 turn 시작 시 자동 evidence로 읽거나 분석할 수도 있다.

## Code — owner

| Tool | 기능 |
| --- | --- |
| `code_index` | Python repository의 import/class/function/route/tool/config/test 구조를 compact index로 생성 |
| `code_search` | 생성된 structural code index에서 관련 파일/symbol 검색 |

## Terminal — owner

| Tool | 기능 |
| --- | --- |
| `terminal_command` | 현재 PC에서 shell command 실행 |

Terminal output encoding은 모델이 고르지 않고 `.env`의 `MAI_TERMINAL_ENCODING`이 결정한다.

## Web / market — owner + trial

| Tool | 기능 |
| --- | --- |
| `latest_search` | 최신성 중심 검색 |
| `web_research` | 검색 → 페이지 읽기 → evidence package 생성 |
| `market_snapshot` | 한국/해외 주식, 지수, FX 등 market snapshot 조회 |

---

# 7. Owner와 Trial

Mai 계정은 두 role로 나뉜다.

### owner

- 전체 tool 사용
- 파일 업로드/다운로드
- filesystem/code/document/image/terminal 접근
- 여러 로그인 session 유지 가능

### trial

- 각 user ID별 독립 graph memory
- core memory capability 사용
- web/market tool 사용
- host filesystem/terminal/code/document/image/upload/download 접근 불가
- **한 trial 계정에는 활성 session을 하나만 허용**

같은 trial ID로 새 기기/브라우저에서 로그인하면 이전 session은 즉시 폐기된다. 따라서 하나의 trial ID를 여러 명이 동시에 공유하는 방식은 지원하지 않는다.

Session token 원문은 DB에 저장하지 않고 SHA-256 hash만 저장한다.

---

# 8. 작업이 브라우저와 분리되는 방식

`POST /chat`은 모델 작업이 끝날 때까지 HTTP request를 붙잡지 않는다.

```text
/chat
→ persistent job 생성
→ worker thread에서 실행
→ browser는 job ID polling
```

따라서 다른 앱을 보거나 페이지를 다시 열어도 서버 작업은 계속된다.

UI 재접속 시:

- 완료된 대화는 `/history`
- 진행 중 작업은 `/chat/jobs`

에서 복원한다.

서버 자체가 종료되면 실행 중이던 job은 성공으로 추측하지 않고 `interrupted` 상태로 남는다.

---

# 9. 설치와 실행

Windows PowerShell 기준이다.

## 9.1 저장소 받기

```powershell
git clone https://github.com/FoggyCaligo/MAI_MyAI_sllm.git
cd MAI_MyAI_sllm
```

## 9.2 Python 환경

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

개발/테스트 의존성까지 필요하면:

```powershell
pip install -r requirements-dev.txt
```

## 9.3 Ollama

Ollama를 실행하고 사용할 모델을 준비한다.

기본 `.env.example`:

```dotenv
MAI_OLLAMA_MODEL=gemma4:e4b
MAI_OLLAMA_IMAGE_MODEL=gemma4:12b
MAI_OLLAMA_BASE_URL=http://127.0.0.1:11434
```

예:

```powershell
ollama pull gemma4:e4b
ollama pull gemma4:12b
```

## 9.4 환경설정

`.env.example`을 참고해 프로젝트 루트에 `.env`를 만든다.

주요 값:

```dotenv
MAI_OWNER_ID=owner
MAI_ALLOWED_USER_IDS=trial-a,trial-b
MAI_HOST=127.0.0.1
MAI_PORT=8000
MAI_GRAPH_DB=data/graph.sqlite3
MAI_CHAT_DB=data/chat.sqlite3
MAI_TERMINAL_ENCODING=utf-8
```

`MAI_OWNER_ID`는 owner 계정이고, `MAI_ALLOWED_USER_IDS`의 나머지 ID는 trial이다.

## 9.5 실행

```powershell
python run_server.py
```

기본 주소:

```text
http://127.0.0.1:8000/
```

외부 HTTPS 공개가 필요하면 Tailscale용 스크립트를 사용할 수 있다.

```powershell
.\start_public_tailscale.cmd
```

---

# 10. 종료

의도된 종료 방법은 **서버를 실행한 터미널에서 `Ctrl+C`**다.

```text
Ctrl+C
→ Uvicorn shutdown
→ FastAPI lifespan cleanup
→ SQLite connection close
→ process 종료
```

Windows terminal에서 `Ctrl+C`가 전달되지 않는 환경이면 `Ctrl+Break`도 사용할 수 있다.

터미널 창 자체를 닫는 방식은 graceful shutdown이 아니므로 권장하지 않는다.

SQLite는 WAL 모드를 사용하므로 실행 중 다음 파일이 보일 수 있다.

```text
graph.sqlite3
graph.sqlite3-wal
graph.sqlite3-shm

chat.sqlite3
chat.sqlite3-wal
chat.sqlite3-shm
```

`-wal`, `-shm`은 별도 logical DB가 아니라 SQLite의 실행 중 보조 파일이다. Mai가 실행 중일 때 임의로 삭제하지 않는다.

자세한 내용은 [`docs/OPERATIONS.md`](docs/OPERATIONS.md)를 참고한다.

---

# 11. 데이터베이스

### `data/chat.sqlite3`

- raw conversation history
- compact recent tool-operation history
- authenticated sessions
- persistent chat jobs

### `data/graph.sqlite3`

- semantic graph nodes
- semantic graph edges
- provenance
- user anchor

개발 중 완전히 새 상태에서 기억 동작을 검증하고 싶다면 Mai를 정상 종료한 뒤 두 DB를 모두 백업/삭제하고 재실행하면 된다.

Graph만 초기화하고 raw 대화는 남기고 싶다면 `graph.sqlite3`만 초기화할 수도 있지만, 최근 chat context가 새 테스트에 영향을 줄 수 있다는 점은 고려해야 한다.

---

# 12. 문서

루트에는 프로젝트를 이해하는 데 필요한 세 문서만 둔다.

- [`README.md`](README.md) — 현재 프로젝트 개요와 사용법
- [`CONTRACT.md`](CONTRACT.md) — 핵심 실행 계약
- [`ROADMAP.md`](ROADMAP.md) — 앞으로의 구현 계획

세부 runtime/계약/운영 문서는 [`docs/`](docs/) 아래에 있다.
