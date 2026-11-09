# MAI 실행 구조

현재 `dev` 코드 기준. 사용자 설치·모델 다운로드: [setup.md](../setup.md). 이전 설명: [.old/architecture.md](.old/architecture.md).

## 1. 전체 구조

```text
브라우저 UI (로그인·대화·모델 선택)
  ↓
FastAPI (인증·작업 관리·감사 로그)
  ↓
MAIRuntime (모델·도구·기억·Verifier 조립)
  ↓
AgentRuntime / AgentLoop
  ├─ llama.cpp Main ↔ 도구 호출
  ├─ 검증: 수치 사전 검사 → 최종 사실 검증 → 선택적 품질 검토
  │    └─ 반려 시 Main이 재조사·수정
  └─ 승인 후 대화 저장·비동기 기억 추출
```

| 구성 | 주요 코드 | 역할 |
|---|---|---|
| 웹·계정·작업 | `mai/app/server.py`, `resumable_chat.py` | 인증, HTTP API, 채팅 작업·복구, 감사 |
| 조립 | `mai/app/runtime.py` | 계정별 도구·모델·기억·검증기 선택 |
| 에이전트 | `mai/agent/runtime.py`, `loop.py` | Main 요청, 도구 반복, 답변 초안·교정 |
| 추론 | `mai/llm/llamacpp.py` | llama.cpp OpenAI 호환 API 요청 |
| 모델 상주·전환 | `mai/llm/llamacpp_router.py` | 모델 상태 조회·언로드·로드 확인 |
| 검증 | `mai/agent/verification.py` | 수치·사실·선택적 품질 검토 |
| 기억 | `mai/memory/` | 추출, 그래프, SQLite FTS5 검색 |
| 도구 | `mai/tools/`, `mai/agent/tool_results.py` | 로컬 작업 및 결과 원문·페이지네이션 |
| 원격 PC | `mai/remote/` | 인증된 원격 Native 워커 호출 |
| 실행·설치 | `mai.sh`, `mai.ps1`, `portable/` | OS별 설치·실행 및 GGUF 카탈로그 |

## 2. 한 번의 사용자 요청

| 순서 | 실행 |
|---|---|
| 1 | 계정 확인, 이전 동일 사용자 비동기 기억 저장 작업 대기 |
| 2 | 최근 대화·선택 모델 확인, 요청별 도구 등록소·결과 저장소 생성 |
| 3 | Main 모델 추론 → 필요할 때 도구 활성화·실행 → 임시답변 |
| 4 | Verifier 검증 → 승인 또는 근거를 첨부한 반려 |
| 5 | 반려 시 Main 재진입·재조사·수정. 교정 예산 소진·필수 계약 위반은 실패로 표시 |
| 6 | 승인 답변 전달·대화 저장, 비동기 기억 추출·저장 |

**수명:** 도구 활성화 집합·도구 결과 저장소·작업 문맥은 요청 단위. 장기기억·대화·감사 DB는 지속 저장. 서버 재시작 후 실행 중 계산을 그대로 재개하는 영속 작업 큐는 아님.

## 3. 모델과 Verifier

| 항목 | 현재 동작 |
|---|---|
| 추론 백엔드 | **llama.cpp 전용**. Ollama 어댑터 없음 |
| 모델 선택 | 시작 시 GGUF 파일 카탈로그 생성. Owner UI에서 선택 가능 |
| 동시 상주 | llama.cpp 라우터 `--models-max 1` |
| 역할별 모델 | **Main·Verifier·기억 추출 = 동일한 선택 GGUF** |
| 추론 요청 | Main은 `think=True`; Verifier·기억 추출은 별도 요청·프롬프트 |
| Main → Verifier | 동일 모델이 `loaded`이면 강제 언로드·재로드하지 않음 |
| KV Cache | 역할 사이 KV 공유·복원 보장 없음 |
| 다른 GGUF 선택 | 이전 모델 `unloaded` 확인 → 새 모델 `loaded` 확인 |
| 모델 전환 실패 | 오류 보고. 다른 모델로 자동 대체하지 않음 |

Verifier 순서: **수치 대조 → 사실·작업 근거 검증 → (활성화된 경우) 품질 검토**. 배포 설정 `MAI_ANSWER_QUALITY_REVIEW: false`, `MAI_VERIFIER_BLOCKING_MODE: critical`. 숫자·사실 검사는 품질 검토를 끄더라도 수행.

- 검증 입력: 현재 사용자 요청, 이전 대화의 노출 가능한 본문, 임시답변, 해당 요청의 성공한 도구 결과·실행 인수.
- 제외: Main의 비공개 thinking, 이전 반려 초안의 일반 Assistant 증거화, 감사 DB의 자동 주입.
- 반려: 가장 최근 초안과 피드백만 **시스템 교정 문맥**에 유지. 근거로 취급하지 않음.
- 실패: 필수 구조화 응답·도구 계약 위반을 성공으로 바꾸지 않음. 미검증 초안은 진단으로만 구분.

세부 사항: [Verifier](verifier.md), [GGUF 모델 선택](llamacpp-gguf-model-selection.md).

## 4. 도구 호출과 실행 대상

| 항목 | 계약 |
|---|---|
| 도구 발견 | 시스템 카탈로그에 이름·분류 노출 |
| 초기 실행 가능 도구 | `tool_manual` |
| 도구 활성화 | 정확한 이름으로 활성화 → 다음 모델 차례부터 입력 스키마 노출 |
| 도구 호출 | 구조화된 `tools` / `tool_calls`. 텍스트 추측·패턴 추출로 복원하지 않음 |
| 인수 검증 | Pydantic 스키마·권한·활성화 상태 검사 |
| 실패 처리 | 미활성·미지원·잘못된 입력·실행 실패·시간초과를 명시적으로 보고 |
| 큰 결과 | 최초 페이지 + 원문 식별자. 추가 내용은 `tool_result_read` |
| 웹 결과 | 최초 원문 노출 뒤 후속 문맥에 출처 기반 발췌 사용 가능. 감사에는 원문 보존 |

**원격 Native 도구:** Owner가 `is_remote=true`로 호출하면 인증된 Windows/Linux 워커에서 실행. 생략하거나 `false`이면 MAI 호스트에서 실행. 원격 미연결·미지원 시 로컬로 자동 대체하지 않음.

**웹 공개는 별개:** `REMOTE_HOSTING_PROVIDER`의 Tailscale/Cloudflare 설정은 브라우저의 외부 접속용이며 원격 PC 도구 실행 권한을 생성하지 않음.

참조: [도구 결과](TOOL_RESULT_PAGINATION.md), [원격 워커](remote-native-worker.md), [외부 웹 공개](remote-hosting.md).

## 5. 데이터·권한

| 데이터 | 기본 경로 |
|---|---|
| 장기기억 | `MAI_MEMORY/memory.sqlite3` |
| 분해기 누적 데이터 | `MAI_MEMORY/sentence_breaker.sqlite3` |
| 대화 | `MAI_MEMORY/chat.sqlite3` |
| 감사 | `MAI_MEMORY/log.sqlite3` |
| 업로드 | `mai_uploads/` |

- **Owner:** 호스트 파일·터미널 및 승인된 원격 Native 도구 사용.
- **체험 계정:** 허용된 읽기 기능과 자기 업로드 영역의 제한적 쓰기. Owner 모델 선택·원격 관리 불가.
- **계정 식별:** 로그인 ID와 데이터 소유 식별자 `db_id` 분리.
- **기억 저장:** 승인 후 사용자 진술 및 허용된 성공 도구 결과에서 사실 추출 → 사용자별 그래프·개념 색인. 별도 비동기 작업.
- **기억 검색:** 필요할 때 `memory_search`로 호출. 매 사용자 턴에 기억 전체를 자동 삽입하지 않음.
- **보안:** `.env`, 업로드, 개인 SQLite, 원격 인증 키는 Git에 올리지 않음.

참조: [기억](memory.md), [로그인 제한](login-ip-lockout.md).

## 6. 실행·설정·검증

```bash
./mai.sh setup
./mai.sh check
./mai.sh start
```

Windows Git Bash / Ubuntu 24.04 Bash 공통. Windows PowerShell은 `mai.ps1`, CMD는 `mai.cmd`. **llama.cpp 바이너리 업데이트**는 `./mai.sh update` 계약을 따름.

| 설정 | 기본값 |
|---|---|
| `LLM_BACKEND` | `llamacpp` |
| `LLAMACPP_HOST` | `http://127.0.0.1:8080` |
| `MODEL_MAX_OUTPUT_TOKENS` | `32768` |
| `MODEL_REQUEST_TIMEOUT_SECONDS` | `300` |
| `SESSION_HISTORY_MESSAGES` | `10` |
| `MAX_INLINE_TOOL_RESULT_CHARS` | `8000` |
| `MAI_ANSWER_QUALITY_REVIEW` | `false` |
| `MAI_VERIFIER_BLOCKING_MODE` | `critical` |

설정 우선순위: **프로세스 환경변수 → `.env` → `settings.yaml`**. Python 패키지 의존성은 `pyproject.toml`; 모델 GGUF는 별도 준비.

```bash
python -m pytest -q
python scripts/audit_references.py
python scripts/audit_codebase.py
```

정적 코드 분석은 **미사용 확정 또는 테스트 커버리지 측정이 아님**. 이전 정적 감사 원본: [.old/REFERENCE_AUDIT.md](.old/REFERENCE_AUDIT.md).

**검증 한계:** 코드에 구현된 기능과 실제 Windows/Linux GPU·네트워크·브라우저·원격 워커에서 정상 동작한 기능은 구분한다. 테스트 실패를 정상 상태로 간주하지 않는다.
