# MK5

`MK5`는 그래프를 사고의 본체로 두던 흐름에서 한 걸음 물러나, **그래프를 기억 전용 인프라로 단순화하고 LLM이 도구를 오케스트레이션하는 실행형 에이전트**다.

이전 구조는 그래프 위에서 직접 사고하는 데 강점이 있었지만, 그만큼 파이프라인이 무거워지고, 응답 경로가 복잡해지며, 유지보수 부담도 커진다. `MK5`는 이 문제를 해결하기 위해 그래프의 역할을 다시 줄인다.

핵심 생각은 단순하다. 그래프는 "생각하는 주체"가 아니라, **사용자와 세계에 대한 장기 기억을 저장하고 꺼내 오는 저장소**가 되면 된다. 응답 계획, 도구 선택, 최종 문장 생성은 LLM이 맡는다.

## 목표

`MK5`의 목표는 다음과 같다.

> 그래프는 장기 기억과 회수만 맡고, 실제 응답 계획과 도구 사용 판단은 하나의 대화 LLM이 담당하게 만든다.

즉 이 단계가 고치려는 문제는 아래와 같다.

- 그래프 사고 루프가 기본 응답 경로를 너무 무겁게 만드는 문제
- 기억과 사고가 과하게 결합되어 구조가 복잡해지는 문제
- 도구 설명과 실행 기록이 너무 길어져 작은 로컬 모델이 문맥을 놓치는 문제

## 기능

### 1. 사용자별 장기 기억

- 각 사용자에 대해 지속적인 anchor를 만든다.
- 같은 사용자 ID로 들어온 정보는 같은 장기 기억 아래에 누적된다.

### 2. 그래프 기반 기억 저장

- 사용자 발화
- 중요 사실
- 검색에서 얻은 정보
- correction / conflict 단서

이런 항목을 장기 그래프에 쌓는다.

### 3. 도구 기반 회수와 작업 수행

- 그래프를 직접 사고 엔진처럼 돌리지 않는다.
- 필요할 때 LLM이 그래프 조회, 웹/최신 정보 검색, 파일 작업, 문서 읽기, 이미지 분석, 터미널 실행 같은 도구를 호출한다.
- 검색이나 검증이 필요한 요청은 모델이 목적을 간결하게 정리해 `web_research`를 호출한다.
- 도구 실행이 필요한 요청에서는 실제 도구 성공 여부를 확인한 뒤 최종 답변을 만든다.

### 4. 가벼운 응답 경로

- 기본 응답은 `현재 메시지 + 최근 대화 + 최근 도구 작업 요약 + 활성도 가중 기억 요약 + 필요 시 도구 호출`로 간다.
- 복잡한 thought loop를 기본값으로 두지 않는다.
- 긴 프롬프트 규칙을 계속 덧붙이기보다, JSON contract와 구조적 guard로 제어한다.

### 5. 도구 이름과 지연 명세 조회

- 모델에게는 모델 가시성 정책을 통과한 도구 이름만 노출한다.
- 도구 인자가 헷갈릴 때는 `tool_manual`을 호출해 해당 도구의 전체 설명과 `input_schema`를 읽는다.
- 이렇게 하면 파일 작업 중 검색이 필요하거나, 이미지 분석 중 터미널 확인이 필요한 경우처럼 서로 다른 도구를 섞어야 하는 턴도 막히지 않는다.

### 6. 파일, 문서, 이미지 입력

- 파일 경로 탐색은 `file_search`, 텍스트 파일 작업은 `file_read`, `file_create`, `file_update`, `file_delete`로 다룬다. 소유자가 PC 파일을 휴대폰 등 현재 브라우저로 내려받으려면 `file_download_link`로 1시간짜리 링크를 만든다.
- `file_search`는 기본적으로 workspace 아래를 재귀 검색하며, `root`에 부모·절대 경로를 주면 workspace 밖도 탐색할 수 있다. 파일 CRUD·문서·이미지·터미널 도구도 부모·절대 경로를 해석할 수 있다.
- `.txt`, `.md`, `.markdown` 파일을 `file_read`로 읽으면 파일 본문에서 핵심 노드 후보를 뽑아 그래프 DB에 파일 문맥 노드로 기록하고 현재 턴의 국소활성화에 편입한다.
- 파일에서 온 노드는 사용자 발화 노드보다 약한 `0.25` 활성 강도로 들어가며, 사용자 장기 기억 summary 후보에서는 제외된다.
- PDF/DOCX는 `document_read`로 텍스트를 추출한다.
- PNG/JPEG/WEBP/BMP/GIF 이미지는 `image_analyze`로 메타데이터와 시각 설명을 얻는다.
- UI에서는 파일 첨부를 지원하며, 업로드된 파일은 `.mk5_uploads/` 아래에 저장된다.

## 작동방식

`MK5`의 흐름은 아래와 같다.

```text
사용자 입력
-> user anchor 확인/생성
-> 발화 저장
-> 사용자 기억 요약 로드
-> 이전 작업 활성도를 불러와 기억 회수 점수에 반영
-> 현재 입력 기준 기억 활성도 계산
-> LLM이 답변 계획 수립
-> 필요하면 graph/web_research/file/document/image/terminal tools 호출
-> 최종 답변 생성
-> 사용자 발화·추출 사실, 검색 결과·검색 사실, 명시적 기억 정정을 그래프에 기록
-> 이번 턴 기억 활성도 상태 보관
```

즉 `MK5`에서는 그래프가 먼저 생각하고 LLM이 번역하는 것이 아니라, **LLM이 계획하고 그래프는 기억 저장소로 지원**한다.

## 기억구조

`MK5`의 기억 구조는 이전 세대보다 훨씬 단순하다.

### 1. 사용자 anchor

- 예: `user_anchor::<user_id>`
- 같은 사용자의 기억을 묶는 기준점 역할을 한다.

### 2. 장기 그래프 저장소

- SQLite 기반 그래프 저장소에 노드와 관계를 유지한다.
- 사용자 발화 흔적과 회수 가능한 사실이 여기에 누적된다.

### 3. 회수용 이웃 구조

- 현재 질문에 맞는 기억은 anchor 주변 이웃과 관련 노드 검색으로 가져온다.
- 그래프는 추론 본체가 아니라 retrieval substrate다.

### 4. 턴 단위 기억 활성도 상태

- 이전 턴에서 활성화된 노드 ID와 가중치를 세션 단위로 짧게 보관한다.
- 현재 질문의 기억 요약을 계산할 때 이 활성도를 관련성 가중치로 사용한다.
- 활성 노드 목록 자체를 별도 모델 입력 필드로 주입하지는 않는다.

즉 `MK5`의 기억 구조는 **사용자 anchor + 장기 그래프 + 도구 기반 회수 + 턴 단위 기억 활성도 상태**로 요약할 수 있다.

### 그래프를 장기 기억으로 유지하는 이유

`MK5`가 기억 저장소로 그래프를 유지하는 이유는 단순한 문자열 검색을 넘어서, 반복되는 개념을 공유하고 그 개념이 형성한 주변 관계를 명시적으로 보존하기 위해서다. 문장 단위 VectorDB와 비교했을 때 다음 두 가지 장점을 기대할 수 있다.

1. **반복 개념을 공유하여 장기 축적 비용을 줄일 수 있다.**

   동일하게 정규화된 단어나 개념은 내용 기반 ID를 가진 하나의 concept 노드로 합쳐지고, 여러 발화와 사실이 그 노드를 공유한다. 같은 관계가 반복되면 edge를 계속 복제하는 대신 support와 가중치를 강화한다. 따라서 같은 개념이 여러 문맥에서 반복되는 장기 데이터에서는, 각 문장마다 고차원 embedding과 별도 인덱스를 저장하는 문장 단위 VectorDB보다 적은 용량으로 공통 개념과 관계를 축적·탐색할 가능성이 있다. 이 장점은 모든 데이터에서 절대적으로 보장되는 것이 아니라, 반복 개념과 공유 관계가 충분히 많은 MACHI의 장기 기억 구조를 전제로 한다.

2. **한 개념의 사용자 고유 의미를 주변 관계망으로 보존할 수 있다.**

   VectorDB의 embedding도 단어나 문장의 의미적 특징을 수치로 표현할 수 있지만, 그 의미가 어떤 발화·사실·출처·정정 관계에서 형성됐는지를 사람이 직접 읽거나 관계별로 탐색하기는 어렵다. 그래프에서는 특정 concept 노드 주변의 발화, 인접 개념, 사용자 사실, 검색 근거, 수정 이력을 함께 살펴봄으로써 그 노드가 MACHI와 사용자에게 축적해 온 전체적인 의미와 맥락을 명시적인 개념 구조로 회수할 수 있다.

이 장점을 모델 응답에 활용할 때는 edge를 대량으로 그대로 주입하지 않는다. 자동 기억 회수와 `graph_search`는 **중심 노드 + 중요한 주변 관계 + 근거 발화·사실**로 구성된 작은 서브그래프 요약을 제공한다.

## 모델 입력 구성

한 턴에서 LLM이 받는 정보는 크게 아래 묶음으로 정리된다.

- 짧은 system prompt
- 요청 시점의 로컬 날짜를 `Current date: YYYY-MM-DD` 한 줄로 동적 주입
- 현재 사용자 입력
- 최근 대화 일부
- 직전 턴까지의 압축된 도구 작업 일부
- 활성도 가중치가 반영된 현재 입력 기준 memory summary
- 모델에 공개된 도구 이름 목록
- 현재 턴의 압축된 tool history
- JSON 출력 계약

현재 최근 대화 기본 개수는 `MK5_RECENT_MESSAGE_LIMIT=10`개 메시지로, 사용자와 assistant 메시지를 한 쌍으로 보면 약 5턴이다. 직전 도구 작업은 도구명, 축약 인자, 성공 여부, 오류와 짧은 결과 요약을 중복 제거한 뒤 최대 5개 보관한다. 현재 턴에서 실행된 도구의 결과도 `tool_history`에 도구별로 압축되어 다음 모델 라운드에 전달된다. 모델에는 도구 이름만 기본 제공하고, 전체 설명과 schema는 필요한 경우 `tool_manual`로 조회한다.

그래프 기억 요약은 기본 최대 5개(`MK5_MEMORY_SUMMARY_LIMIT=5`)만 자동 주입한다. 현재 발화와의 직접 관련성 또는 그래프 활성도 신호가 `MK5_MEMORY_SUMMARY_MIN_SIGNAL` 기준을 통과한 항목만 포함하므로, 관련 기억이 부족하면 5개를 억지로 채우지 않는다. 각 기억은 원문 노드나 edge 목록 전체가 아니라 **중심 기억 + 핵심 관계 최대 4개 + 회수 근거·출처**의 작은 서브그래프로 압축된다. 자동 요약만으로 과거 발언·선호·결정·프로젝트 문맥이 부족하면 모델은 기억이 없다고 답하기 전에 `graph_search`로 추가 조회한다.

텍스트 파일에서 만들어지는 노드는 기본적으로 상위 70%, 최대 24개만 유지한다. 이 값은 `MK5_FILE_TEXT_NODE_KEEP_RATIO`, `MK5_FILE_TEXT_NODE_MAX_ITEMS`로 조정할 수 있다. 후처리 입력은 기본 8,000자로 제한되며 `MK5_FILE_TEXT_ACTIVATION_MAX_CHARS`로 조정한다. 파일 문맥 노드는 출처 추적을 위해 그래프 DB에는 기록되지만 `suppress_from_summary`가 설정되므로 사용자 발화·사실 기반 장기 기억 summary 후보로 자동 주입되지 않는다.

도구 라운드 수에는 고정 상한이 없다. 모델이 JSON 출력 계약을 반복해서 깨거나 존재하지 않는 도구를 반복 호출하면 회로차단기가 응답을 멈춘다. 같은 도구와 인자를 기본 3회보다 많이 반복해도 정체로 판단하고 최종 합성으로 전환한다.

JSON 파싱 실패가 발생하면 서버 콘솔의 `[MK5 model] output_parse_failed` 로그에 구체적인 파싱 오류, 원문 길이, 실패 출력 preview가 기록된다. preview 기본 상한은 2,000자이며 `MK5_MODEL_FAILURE_PREVIEW_CHARS=0`으로 원문 기록을 끌 수 있다. Ollama의 HTTP 400 요청 거부는 JSON 파싱 실패와 별도로 표시된다.

웹 조사가 필요하면 모델이 사용자 입력에서 주제, 구분 문맥, 확인할 사실을 담은 간결한 목적을 만들어 `web_research`에 전달한다. `web_research`는 복수 검색 소스 조회, 결과 랭킹, 상위 페이지 읽기와 관련 구간 추출을 서버 측에서 수행한다. 그래프 노드나 노드 조합은 검색어 생성과 실행 여부 판정에 사용하지 않는다. 저수준 `internet_search`와 `web_page_read`는 내부 구현으로만 유지하며 모델 도구 목록과 매뉴얼에서는 숨긴다.

## 주요 도구

현재 기본 도구군은 다음과 같다.

- `graph_search`: 장기 그래프 기억을 중심 노드 + 핵심 관계 최대 6개 + 출처의 작은 서브그래프로 검색. 최초에는 `query`를 사용하고, 반환된 관계의 `node_id`를 다음 호출에 전달하면 해당 노드를 정확한 중심점으로 확장한다. 사용자 ID는 모델이 정하지 않고 현재 요청의 실제 사용자로 강제된다.
- `record_memory_correction`: 기억 수정/정정 기록
- `web_research`: 자연어 검색 목적을 받아 복수 검색, 결과 랭킹, 페이지 읽기, 관련 구간 추출까지 수행
- `latest_search`: 최신성 정보가 필요한 질문용 검색
- `market_snapshot`: 한국 주식(종목명/코드), 해외 주식, 주요 지수(KOSPI, KOSDAQ, S&P500, NASDAQ), 환율(USD/KRW) 등의 실시간/지연 시세 조회
- `file_search`: 작업공간에서 정확한 파일 경로 검색
- `code_index`: Python 코드의 import, 클래스, 함수, route, 도구, 설정, 테스트 구조를 압축 인덱싱
- `code_search`: 압축 코드 인덱스에서 관련 파일과 symbol 검색
- `file_create`, `file_read`, `file_update`, `file_delete`: 파일 CRUD
- `file_download_link`: 소유자 전용 임시 파일 다운로드 링크 생성
- `document_read`: PDF/DOCX 텍스트 추출
- `image_analyze`: 이미지 분석
- `terminal_command`: 터미널 명령 실행
- `tool_manual`: 특정 도구의 상세 설명과 schema 조회

사용자가 과거 대화의 내용·주제·세부사항을 회상해 달라고 했는데 모델이 자동 기억만으로 최종 답변을 만들면, 완료 검증기가 그 답변을 보류하고 `graph_search`를 한 번 실행한 뒤 검색 결과를 포함해 다시 답하도록 한다. 이때 현재 질문 자체의 발화 노드는 검색에서 제외한다.

도구 목록은 UI의 `/tools` 엔드포인트에서도 확인할 수 있다.

`file_download_link`는 워크스페이스 상대 경로, 상위 경로와 절대 경로의 파일을 받을 수 있다. 링크는 기본 60분 동안 유효하다. `/download/{token}`은 로그인한 `owner` 세션에서만 열리며 `trial` 계정과 로그아웃 상태에서는 거부된다. 서버를 재시작하면 메모리에 있던 링크도 모두 무효화된다. UI는 도구 결과를 모바일에서 누르기 쉬운 다운로드 버튼으로 표시한다.

## 외부 접속 계정

웹 UI와 API는 `MK5/.env`의 `MK5_ALLOWED_LOGIN_IDS` 목록에 등록된 문자열만 허용한다. 값은 JSON 배열 또는 쉼표 구분 문자열로 추가할 수 있다. `default-user`와 목록에 없는 값은 거부되고 UI에는 로그인 모달이 표시된다. 로그인에 성공하면 서버가 `HttpOnly`, `SameSite=Strict` 세션 쿠키를 발급하며, `/chat`의 그래프 사용자 ID는 요청 본문이 아니라 로그인 정보에서 강제한다.

- `owner`: 기존 개인 그래프 기억에 연결할 수 있고 모든 도구와 파일 업로드를 사용할 수 있다.
- 나머지 허용 ID: 각각 별도 `trial` 그래프 사용자로 격리되며 `graph_search`, `record_memory_correction`, `latest_search`, `market_snapshot`, `web_research`, `tool_manual`만 사용할 수 있다. 파일·문서·이미지·코드·터미널 도구와 업로드는 서버에서 차단한다.

`MK5/.env`는 저장소에 커밋되지 않는다. 예시는 다음과 같다.

```dotenv
MK5_ALLOWED_LOGIN_IDS=["owner-secret-id","family-id","friend-id"]
MK5_OWNER_LOGIN_ID=owner-secret-id
MK5_OWNER_GRAPH_USER_ID=existing-graph-user-id
MK5_MAX_ACTIVE_SESSIONS=3
```

`MK5_OWNER_LOGIN_ID`와 일치하는 한 계정만 `owner` 권한을 얻는다. 다른 허용 ID는 로그인 문자열의 SHA-256에서 안정적인 그래프 사용자 키를 파생하므로 서로 기억이 섞이지 않는다. 허용 ID 문자열을 바꾸면 새 그래프 사용자로 취급된다. 활성 로그인 세션은 `MK5_MAX_ACTIVE_SESSIONS`개로 제한되며 기본값은 3이다. 같은 브라우저에서 다시 로그인하면 기존 세션을 교체하므로 슬롯을 추가 소비하지 않는다.

브라우저에는 ID 원문 대신 무작위 `HttpOnly` 세션 토큰만 저장한다. 서버는 토큰의 SHA-256 해시를 `MK5/data/sessions.db`에 보관하므로 브라우저나 MK5를 다시 시작해도 기본 168시간 동안 로그인이 유지된다. `.env` 허용 목록에서 ID를 제거한 뒤 서버를 다시 시작하면 그 계정의 기존 세션도 다음 요청에서 자동 폐기된다. `MK5_SESSION_TTL_HOURS`로 유지 시간을 조정할 수 있다.

현재 설정을 원문 ID 노출 없이 확인하려면 다음 명령을 사용한다.

```powershell
python manage_accounts.py list
```

특정 허용 ID의 그래프 기억을 지우려면 로컬 터미널에서 명시적 확인 문구를 입력해야 한다. 계정 자체를 막으려면 별도로 `.env` 목록에서 해당 ID를 제거하고 서버를 다시 시작한다.

```powershell
python manage_accounts.py purge-memory
```

이 작업은 해당 그래프 사용자 anchor, 사용자 소유 노드, 사용자 ID가 기록된 edge와 그 결과 고아가 된 concept 노드를 삭제한다. 복구가 필요하면 실행 전에 `MK5/data/memory.db`를 백업해야 한다.

서버는 기본적으로 안전한 로컬 주소 `127.0.0.1:8010`에 바인딩한다. LAN에서 직접 열어야 할 때만 `MK5_SERVER_HOST=0.0.0.0`을 명시한다. 인터넷 HTTPS 주소 뒤에서 사용할 때는 `MK5_SESSION_COOKIE_SECURE=true`로 실행해야 한다.

도메인 없이 재사용 가능한 외부 HTTPS 주소는 Tailscale Funnel을 사용한다. Tailscale을 설치하고 로그인한 뒤 MagicDNS와 HTTPS/Funnel 사용을 승인하고 다음 스크립트를 실행한다.

```powershell
.\start_public_tailscale.cmd
```

`.cmd` 실행기는 시스템 전체 PowerShell 정책을 변경하지 않고 이 프로젝트의 `start_public_tailscale.ps1`에만 `ExecutionPolicy Bypass`를 적용한다. 정책상 `.ps1` 직접 실행이 허용된 환경에서는 기존 스크립트를 그대로 실행해도 된다.

스크립트는 MK5를 `127.0.0.1:8010`에서 숨김 프로세스로 실행하고 secure session cookie를 활성화한 뒤 `tailscale funnel 8010`을 연결한다. Funnel은 이 장치의 예측 가능한 `https://<장치>.<tailnet>.ts.net` 주소를 공개 인터넷에 제공한다. 장치가 켜져 있고 Tailscale과 MK5가 실행 중일 때만 접속할 수 있으며, Funnel 자체가 공개 인터넷 경로라는 점은 변하지 않는다.

## 실행방법

아래는 Windows PowerShell 기준으로, 빈 PC에서 새로 내려받아 실행하는 순서다.

### 1. 저장소 받기

```powershell
git clone https://github.com/FoggyCaligo/MACHI.git
cd MACHI\MK5
```

### 2. 가상환경 만들기

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

`requirements.txt`에는 서버 실행, 검색, 문서 읽기, 이미지 분석, 파일 업로드에 필요한 의존성이 포함되어 있다. 특히 업로드 기능에는 `python-multipart`, 이미지 메타데이터/검증에는 `pillow`가 필요하다.

### 3. Ollama 준비

기본 대화 모델은 `gemma4:e4b`이고, 이미지 분석 모델은 별도로 지정할 수 있다.

```powershell
ollama serve
```

새 PowerShell 창에서:

```powershell
ollama pull gemma4:e4b
ollama pull gemma4:12b
```

필요하면 환경변수로 모델을 바꾼다.

```powershell
$env:MK5_OLLAMA_MODEL_NAME="gemma4:e4b"
$env:MK5_OLLAMA_IMAGE_MODEL_NAME="gemma4:12b"
$env:MK5_OLLAMA_IMAGE_FALLBACK_MODEL_NAME="gemma4:12b"
```

### 4. 서버 실행

```powershell
python run_server.py
```

### 5. 접속 확인

브라우저에서 아래 주소를 연다.

```text
http://127.0.0.1:8010/
```

구현을 볼 때 시작점으로 좋은 파일은 아래다.

- `app/server.py`
- `app/pipeline.py`
- `core/agent/orchestrator.py`
- `core/graph/repository.py`
- `core/graph/service.py`
- `tools/graph_tools.py`
- `tools/web_search.py`
- `tools/workspace_tools.py`
- `tools/document_tools.py`
- `tools/image_tools.py`
- `tools/manual_tools.py`
- `docs/architecture.md`

## 다른 레포에서 사용하기

`MK5`는 `playlist2` 같은 다른 레포에서 그래프 메모리와 도구 오케스트레이션 엔진으로 가져다 쓸 수 있다.

이때 호스트 레포별로 아래 경로를 분리해서 지정하는 것이 좋다.

- `MK5_WORKSPACE_ROOT`
- `MK5_DB_PATH`
- `MK5_SENTENCE_BREAKER_DB_PATH`

자세한 예시는 `docs/using_mk5_from_other_repo.md`를 참고하면 된다.
