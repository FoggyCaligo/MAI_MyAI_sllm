# Turn 01 — MAI 레거시 전체 감사 및 Rust 이식 기준선
> 기준: `python/` = `recovery/flag-mcp-20261114` / `c43010dfa2d1b9bc672623fbf02d2eb2544f4fc8`
> 작성: 2026-11-14. 정적 Git 저장소 감사. **실행·Windows 장치·실제 DB 검증은 수행하지 않았다.**
> 연관: `behavior-test-spec.md`, `consolidation-opportunities.md`, `legacy-file-inventory.json`.

## A. 감사 대상과 증거 수준
- Git 트리 전체: **376개 추적 blob**. 코드·테스트·문서·런처·설정·UI·벤치마크를 포함. 비교 기준의 모든 원본 blob은 `python/`에 SHA 일치로 보존되어 있다.
- 전체 파일 **경로·크기·해시·도메인 분류**를 `legacy-file-inventory.json`에 기록.
- 핵심 구현을 직접 열람: `mai/agent/{loop,runtime,guards}`, `mai/app/{server,runtime,resumable_chat,chat_jobs}`, `mai/llm/{llamacpp_router,llamacpp,cloud_api}`, `mai/tools/{registry,tool_manual,__init__}`, `mai/remote/{routing,session,workspace,agent_client,python_worker,temp_workspace}`, `mai/memory/{runtime,segmenter,recall/query,graph/schema,index/sqlite_fts}`, `portable/llamacpp_memory_gateway`, `settings.yaml`, `.env.example`.
- 테스트 파일명은 전수 목록화; 대표 행동 테스트 16개 파일의 테스트 함수명·주요 assertion을 직접 확인. **각 파일을 모두 줄 단위로 심층 리뷰했다는 의미는 아니다.** 나머지 구현·테스트는 후속 감사 체크리스트에 포함하며, 본 문서에 적은 세부 계약은 확인한 코드에 한정한다.
- **증거 등급**: [C] 코드에서 직접 확인, [T] 테스트 assertion에서 확인, [S] 설정/문서상 요구, [P] 제안/추가 검증 필요. 구현시 C/T/S 충돌을 실제 동작 재현으로 해소한다.

## B. 전체 도메인·모듈 인벤토리
| 도메인 | 주요 원본 | 현재 역할과 보존 정책 |
| --- | --- | --- |
| 웹/API | `mai/app/server.py`, `resumable_chat.py`, `chat_jobs.py`, `chat_sessions.py` | [C] FastAPI 로그인/채팅/모델/음성/업로드/원격 세션; 별도 비동기 job·poll·cancel. **UI 계약 유지**, Rust 서버와 한 job service로 통합 검토 |
| Web UI | `mai/app/static/index.html`, `new-chat.js`, `resumable-chat.js`, `voice-input.js`, `remote-agent-*.{ps1,sh}` | [S] 화면/흐름 유지; API와 정적 파일 제공 방식만 Rust에 연결 |
| 에이전트 | `mai/agent/loop.py`, `runtime.py`, `guards.py`, `tool_results.py` | [C] tool loop, lazy activation, 중복 실패/진행 보호, 출력 페이지 처리, 단계별 이벤트, 임시파일 후처리. Verifier 관련 로직은 폐기 |
| 검증기 | `mai/agent/verification.py`, `verifier_prompts/*`, 여러 `test_verifier_*` | **폐기**. 외부 사실 확인 정책은 검증 모델 없이 일반 에이전트 지침으로 분리 검토 |
| 모델 | `mai/llm/llamacpp.py`, `llamacpp_router.py`, `cloud_api.py`, `models.py`, `stream_diagnostics.py` | [C] GGUF router, 모델 전환/로딩/언로딩 및 추론. 외부 API 공급자 지원 유지. 스트림 진단 유지 |
| 설치/모델 배포 | `portable/*`, `mai.{sh,ps1,cmd}`, `MAI_light_instant_runner/*` | [S] 단일 모델 상주, signed Python 포터블 런처, 다운로드 해시 검증, Windows/Linux. Rust 패키징으로 교체하되 기존 복구 경로 보존 |
| 도구 | `mai/tools/*` | [C] Native registry, tool_manual, 파일·문서·코드·터미널·브라우저·웹·마켓·시간·계산·재시작. **MCP 전송과 mcp_input_schema 분기는 제거** |
| 원격 | `mai/remote/*` | [C] owner 연결, polling worker, sticky Host/Client, 작업별 run_id 격리 및 tmp cleanup. Rust native worker로 교체 |
| 메모리 | `mai/memory/*` | [C] 사용자별 SQLite graph + FTS, 팩트/발화·시간 선후관계, 자동·명시 검색, working graph; mcp_memorizer 호출 제거 |
| Sentence_Breaker | `mai/memory/segmenter.py` | [C] **외부 `sentence_breaker.LanguageGraph` 래퍼만 존재**. 실제 알고리즘 소스는 이 저장소에 없음. 해당 프로젝트의 정확한 버전·코드·테스트·DB 스키마를 추가 확보한 뒤 Rust 이식 |
| 음성 | `mai/app/voice.py`, `mai/app/static/voice-input.js` | [C/T] 브라우저 Web Speech와 로컬 전사 경로, 음성 경계·제한·인증 유지 |
| 호스팅/보안 | `mai/app/access.py`, `login_rate_limit.py`, `hosting/*`, `tailscale.py` | [C] owner/trial 분리, 사용자 DB 범위·인증·로그인 제한, Tailscale/Cloudflare 연결 유지 |
| 벤치마크 | `benchmark/GAIA Benchmark`, `LongMemEval Benchmark`, `Agent Comparison` | [T] 데이터 수집·세션 replay·정답 추출·채점·MAI/OpenClaw 비교. MAI 측 자체 runner Rust 이식; 공식 scoring 비교 유지 |
| 테스트 | `tests/*`, `tests/*.cjs` | Rust parity test seed. Verifier/MCP 고유 테스트는 제거 목록에 별도 기록 |
| 문서 | `docs/*`, `README.md`, `setup.md` | 필요한 기능/운영 문서만 Rust 기준으로 재작성; 과거 문서는 `python/`에 보존 |

## C. 확인된 주요 실제 계약
1. [C] `settings.yaml`: 기본 `LLAMACPP_HOST=127.0.0.1:8080`, `LLAMACPP_CONTEXT_SIZE=65536`, Ornith 1.5 9B Q8 기본 GGUF, `MODEL_REQUEST_TIMEOUT_SECONDS=500`, `MODEL_MAX_OUTPUT_TOKENS=32768`, `TOOL_TIMEOUT_SECONDS=60`, `SESSION_HISTORY_MESSAGES=16`; `MAI_MIN_AVAILABLE_RAM_MIB=6144`, `LLAMACPP_MIN_FREE_VRAM_MIB=1024`, `LLAMACPP_CACHE_RAM_MIB=0`; verifier 기본 false. **값이 실제로 소비되는 위치는 각 구현에서 재확인 필요**.
2. [C/T] 모델 로딩: `SingleResidentRouter`는 unload 확인 후 다음 모델 load, 상태 미확인·추정치 없는 로딩·RAM 부족은 실패 처리. `portable/llamacpp_memory_gateway.py`도 같은 Router를 사용하여 공유 upstream을 보호한다. 실제 단일 lock 소유 범위를 추가 확인할 것.
3. [C/T] 도구: `tool_manual`만 최초 tool schema로 노출 → 활성화된 schema가 다음 턴에 추가 → 미활성 호출 거부, 알 수 없는 툴/입력 거부, 필요 호출 활성화 후 가짜 성공 답변 방지. `ToolRegistry`의 `mcp_input_schema` 조건부 JSON Schema는 제거 후보.
4. [C/T] 원격: Host/Client 두 상태만 지원, Client 선택 후 여러 호출에 걸쳐 유지, 연결 실패시 host fallback 금지. 외부 web/browser 도구는 Host-only이고 worker에는 노출되지 않음. 원격 timeout 배수는 정의 위치별로 점검.
5. [C/T] 임시파일: `remote/temp_workspace.py`는 `cwd/tmp/<run_id>/`를 사용하고 범위 이탈·symlink 거부. `AgentRuntime.run()` finally에서 remote cleanup 신호를 보내며, 실패/취소에서도 실행. 기존 내용은 실제 작업 로컬 저장소에 없으므로 단독 재현 불가.
6. [C/T] Job: `ChatJobStore`는 pending/running/completed/failed/cancelled, 계정별 접근·취소·재요청 교체와 stream thinking/툴 진행 업데이트를 관리.
7. [C/T] 메모리: user_id/DB 범위, boolean query, fact/utterance 구분, 임의 유저 검색 유출 금지, 다중 팩트 검색, 기존 SQLite 보존.
8. [C/T] 음성: 짧은 음성 전사, 무음·잘못된 입력 422, voice/config 및 인증; JS 브라우저 STT 실패 처리 등 기존 정적 UI 회귀 테스트 유지.
9. [C/T] 벤치마크: GAIA final-line 답변 추출, 정답 누출 금지, dataset 밖 첨부 금지, 재개 중복 ID 거부; LongMemEval 원문 replay 및 데이터 정렬·무결성·SHA 검증.
10. [C] 외부 LLM: `cloud_api.py`에 OpenAI/Anthropic/OpenRouter 공급자 구성이 존재. Rust 설계에서 이전 문서의 'GGUF 전용' 인상을 수정해야 한다.

## D. 실제 라우트 목록 [C]
`/health`, `/login/session`, `/login`, `/me`, `/logout`, `/voice/config`, `/voice/activity`, `/voice/transcribe`, `/models`, `/models/select`, `/download/{token}`, `/upload`, `/uploads/image/{filename}`, `/session/{session_id}`, `/chat`, `DELETE /session/{session_id}`, `/remote/sessions/current`, `POST /remote/sessions`, `GET/DELETE /remote/sessions/{session_id}`, `/remote/agent/{session_id}/bootstrap`, `/remote/agent/{session_id}/poll`, `/remote/agent/{session_id}/result`, `DELETE /remote/agent/{session_id}`.
**추가**: `resumable_chat.install()`이 추가하는 job/stream 경로는 실제 데코레이터 인자와 JS 호출 트레이스를 일치시켜 고정해야 한다. 이 단계에서 URL을 임의 추정하지 않는다.

## E. 데이터·보안·프로세스 인수 요구사항
- [S] `.env.example`: `MEMORY_DB_PATH`, `SENTENCE_BREAKER_DB_PATH`, `CHAT_DB_PATH`, `LOG_DB_PATH`; 실제 사용자 데이터는 미열람. 기본 경로는 실행 cwd 의존. Rust에서 데이터 경로 canonicalization·기존 위치 탐지 후 별도 migrate 수행.
- [S] `OWNER_USERS`와 `TRIAL_USERS`는 인증 경계 및 `db_id`/memory_user_id 격리 수단. 프로덕션 실계정 비밀번호/토큰을 문서와 테스트에 포함하지 않는다.
- [P] 모델·메모리·업로드·작업 데이터는 서로 다른 수명주기. SQLite 트랜잭션, 실패 후 재개, job 재실행 중복 쓰기, 루트 이동 시 하드코딩 경로 점검.
- [P] C# launcher 및 PowerShell/Bash 설치 경로를 Rust native 패키징으로 교체하되 서명 확인과 롤백의 실효 동작은 별도 테스트.
- [P] 실제 실행 테스트는 장비/모델/토큰/사용자 DB가 있는 로컬 환경에서 수행. 현재 GitHub 정적 감사만으로 latency/메모리 절감, 로컬 tool 실행, 전 테스트 통과를 주장하지 않는다.

## F. 감사를 통해 확인된 설계 변경 사항
- **MCP 고유 구성 제거**(요청 확정). 현재 복구 브랜치에서도 runtime은 Native Tool이며 registry에 `mcp_input_schema` 과거 호환 분기가 남아 있음.
- **Verifier 제거**(요청 확정), 그러나 agent prompt에서 verifier feedback 언급, event payload, `verify` 요청 필드, 관련 설정·테스트까지 추적해야 완전 폐기 가능.
- **Sentence_Breaker는 저장소 내부 구현이 아닌 외부 패키지 의존**이므로 정확한 알고리즘 이식은 그 저장소 감사 없이는 완료 불가.
- **기존 UI는 Vue로 가정하지 않는다**. 이 Git 트리에는 `mai/app/static/index.html` 및 JS가 존재한다. 실제 UI를 그대로 서빙하는 계약이 우선이다.
- 기존 단일 모델 정책을 유지하면서 **클라우드 모델 선택 경로도 유지**한다. 다른 권한의 trial 모델 정책 역시 별도 검증.
- 상세한 통합 후보와 폐기 경계는 `consolidation-opportunities.md`에 정리한다.

## G. 추가 증거 수집 체크리스트 (Turn 01의 실행 기반 보강)
- [ ] 추적된 **모든** 소스의 코드 레벨 import/call graph 및 모든 테스트 assertion 완전 추출.
- [ ] `Sentence_Breaker` 실제 리포지토리/버전·테스트 corpus 및 데이터 스키마 확보.
- [ ] UI 네트워크 실제 HAR 또는 fixture, job route·payload 필드 전수 고정.
- [ ] 사용자 제공 설정에서 실효 timeout/우선순위 점검. 500초는 **모델 호출별인지 총 작업별인지** 코드 재현으로 확정.
- [ ] `python/` 위치로 옮긴 레거시 스크립트들의 상대경로 수정 여부 판단(원본 수정 없이 분석).
- [ ] Rust 테스트 환경에서 각 golden trace 구현 및 Windows/Linux 실기 검증.
