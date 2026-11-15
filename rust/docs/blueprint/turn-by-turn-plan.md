# MAI Rust 재구성: 턴별 구현 순서
> 2026-11-14 초안 · 기준 브랜치: `recovery/flag-mcp-20261114`
> 각 턴은 **하나의 검증 가능한 결과**를 제출하고, 다음 턴에서 이전 결과를 가정하기 전 커밋·테스트·잔여 이슈를 확인한다. 번호는 작업 순서를 의미하며 실제 대화 횟수는 재조정할 수 있다.

## 작업 공통 원칙
- 시작: `git status`, 현재 브랜치/HEAD, 사전 문서·API 계약 확인.
- 구현: 각 턴 목적 외 코드 변경 최소화. 새 Rust 프로젝트 중심, `python/`은 참조용으로 보존.
- 완료: `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test` (환경 허용 시), 관련 계약/통합 테스트. 도구 실행 불가능 시 이유와 미검증 항목 명시.
- 기록: 수행 내용, 변경 파일, 테스트 결과, 의도된 차이, 다음 턴 입력 조건을 문서에 남긴다. 실패 시 덮어쓰지 않고 수정하거나 분리 브랜치에서 되돌린다.
- 전환 검증 전까지 기존 Python 구현과 사용자 데이터 삭제 금지. 각 단계에서 경로 하드코딩을 제거하며 root 기준 변경을 반영한다.

## Turn 00 — 기준선 보관·구조 분리 (이번 턴)
- [x] 기준 복구 브랜치 확인: `recovery/flag-mcp-20261114`
- [x] 신규 재구성 브랜치에서 전체 기존 추적 파일을 `python/`으로 보관
- [x] `rust/docs/blueprint/technical-spec.md`, `turn-by-turn-plan.md` 작성
- [ ] 로컬 clone에서 변경된 전체 파일 무결성 및 실행/빌드 경로 검증
- 완료 기준: 원본 Git blob SHA를 변경하지 않고 위치만 이동, 총 파일 개수 일치, 2문서 존재.

## Turn 01 — 기능·호환성 감사 (정적 감사 산출물 작성, 실행 검증 미완료)
- [x] 376개 전체 파일 경로·SHA·크기·기능 영역 인벤토리 생성; 핵심 구현과 대표 테스트 직접 확인. [ ] 나머지 파일 상세 함수별 호출 관계/모든 assertion 및 실기 경로 감사.
- [x] 주요 API route, 설정, 자원 보호, 권한·모델·원격 수명주기 추출. [ ] 전체 event payload/DB schema/툴 목록/서드파티·라이선스/실효 timeout 확인.
- [x] 유지/폐기/대체 및 인터페이스 통합 후보 보고서 작성. [ ] 외부 Sentence_Breaker 원본 알고리즘·라이선스·테스트 확보.
- 산출물: `legacy-audit.md`, `behavior-test-spec.md`, `consolidation-opportunities.md`, `legacy-file-inventory.json`.
- 미완료 게이트: 모든 핵심 기능/모든 도구·UI 이벤트의 상세 parity mapping과 golden traces. **정적 매핑 작성은 완료했으나 실제 동작 검증은 아직 아니다.**

## Turn 02 — Rust workspace 및 도구 체인
- workspace/crate 골격, Cargo lockfile, 설정 파일/secret 분리, Windows 11 + Linux 빌드 및 CI 초기화.
- 기존 UI 파일 위치·정적 제공 방식 확정; Rust 서버에서 파일 수정 없이 제공할 수 있게 정적 경로 설계.
- 완료 기준: 두 OS에서 최소 서버/단위 테스트 빌드 및 재현 가능한 설정 로딩.

## Turn 03 — API 서버·UI 호환 계약
- 인증/로그인, 상태·세션·채팅 경로, SSE/WebSocket 스트림, 작업 목록과 중지 API.
- UI의 기존 네트워크 트레이스를 고정 fixture와 계약 테스트로 검증.
- 완료 기준: **기존 Web UI 수정 없이** 최소 채팅 요청 및 스트리밍 UI 표시 성공.

## Turn 04 — llama.cpp 모델/자원 관리자
- 외부 router 실행/감시, model catalog, 로딩/언로드/전환, 단일 상주, VRAM/RAM 예산 보호, 에러 복구·로그.
- 기존 Q8 선택·디폴트 값과 실제 기준 브랜치 상태를 비교하여 config 마이그레이션.
- 완료 기준: 모델 전환/자원 부족/크래시 복구 테스트 및 한 모델 상주 보장.

## Turn 05 — Agent Runtime
- user request → prompt/context → streaming inference → tool loop → final; 고정 라운드 상한 없이 취소·정책화된 timeout 적용.
- think/stream 상태, 최종 답변 검증에 필요한 정책만 구현; Verifier 단계 없음.
- 완료 기준: 다중 도구 사이클/중지/실패에서 작업 상태 일관성 유지.

## Turn 06 — Native Tool Runtime
- MCP 없이 Rust registry·schema·tool_manual·boolean 검색·결과 pagination, 파일·터미널·브라우저·검색·문서·코드·계산기 등 기존 필수 도구 기능 구현.
- 샌드박스와 실행 위치(Host/Client), 사용자 권한, 출력 제한, timeout과 로그 일원화.
- 완료 기준: 기존 툴 목록 대비 **필수 기능** 각각 계약 테스트 통과.

## Turn 07 — Host/Client 원격 Worker
- Windows 및 Linux 워커, 지속되는 실행 위치 선택, 인증·재연결·타임아웃·에러 전달.
- 워커 `./tmp` 내 임시 생성 → 최종 호출 종료 신호 → 안전한 정리, 동시 작업 안전성 확보.
- 완료 기준: 같은 tool scenario가 Host/Client에서 동작; 이탈·종료·취소·정리 회귀 테스트 통과.

## Turn 08 — Sentence_Breaker 전면 Rust 이식
- 기존 구현 소스와 테스트를 확정하고 순수 Rust 인터페이스/상태 머신/스트리밍 분할 구현.
- 기존 결과와 동일한 golden fixtures 실행; 알고리즘 및 저작권·라이선스 표기 유지.
- 완료 기준: 합의된 corpus의 동등성 + 성능/메모리 비교 통과.

## Turn 09 — Native Memory
- 기존 MAI SQLite/FTS/graph/working-memory 상태 읽기와 Rust API 구현. mcp_memorizer 연동 제거.
- 데이터 마이그레이션은 backup → dry run → 검증 → 원자적 전환. 프로덕션 DB 직접 덮어쓰기 금지.
- 완료 기준: 검색/기억/복구 계약과 이전 데이터 보존 검증.

## Turn 10 — Web UI 주변 기능·음성
- 기존 Web UI의 파일·스타일 변경 없이 STT/TTS, 음성 목록, 텍스트/음성 요청, UI 상태/중지 계약 연결.
- TTS 오류가 STT를 종료시키지 않음, 음성 입력 유휴 타이머 갱신, 모바일 접근/인증/원격 접속 점검.
- 완료 기준: PC/Android 브라우저에서 기존 UI 기능 회귀 확인.

## Turn 11 — 배포·수명주기·설정
- Windows/Linux native 실행/설치/업데이트/진단/서명/재시작, 외부 llama.cpp 및 GGUF 검증, 로컬/원격 호스팅 구성.
- 루트 README/LICENSE/NOTICE/실행 진입점 및 프로젝트 구조 재정립. 기존 Python 유지 및 롤백 스크립트.
- 완료 기준: 새 PC에서 설치→로그인→모델→채팅→툴→재시작 전체 경로 통과.

## Turn 12 — Benchmarks Rust 이식
- 기존 GAIA, LongMemEval, Agent Comparison의 MAI 어댑터/실행/채점/결과 수집을 Rust CLI로 이식.
- 공식 Python/JS 의존이 필수인 평가자와 데이터 포맷을 구분하고, 이식 가능·불가 항목을 문서화. 공식 scoring과 점수 동등성 확보.
- 완료 기준: 동일 케이스 집합에서 결과 JSON 호환 및 점수 일치; 타 에이전트 벤치마크 인터페이스 손상 없음.

## Turn 13 — 성능·신뢰성·정리
- 동일 모델/하드웨어 기준 Python vs Rust 프로파일링 및 GAIA/LongMemEval 비교.
- 장시간 부하/동시성/원격 단절/메모리 부족/취소 및 파일정리 검증; 오류·비정상 종료 대응.
- Rust 쪽 사용하지 않는 feature/설정/legacy adapter 및 전환 중간 코드 제거. 기존 `python/` 보존 여부는 별도 결정.
- 완료 기준: 전체 parity matrix 통과, 벤치마크 리포트, 알려진 차이·회귀 0 또는 명시적 승인.

## Turn 14 — 전환 의사결정
- 실제 실행 환경에서 단계적 전환, 사용자 DB 백업 및 롤백 리허설, `dev` 병합은 사용자 승인 후 시행.
- 최종 원칙: 성능보다 **기능 보존·데이터 무결성·안전한 복구** 우선.

## 실시간 진행 표
| 턴 | 현재 상태 | 비고 |
| --- | --- | --- |
| 00 | 완료(원격 Git 트리 구조 변경, 로컬 검증 별도) | 이 문서 생성 및 전체 파일 이동 |
| 01 | 정적 감사 작성 / 실기검증 대기 | 376파일 인벤토리, 핵심 코드·대표 테스트, 3개 보고서 |
| 02 | 비동기 Core 초기 구현 완료 / 통합 대기 | cargo fmt/test/clippy CI 성공, 실제 UI·llama.cpp 미연결 |
| 03 | 진행 중 | llama.cpp HTTP 연결과 로컬 API 미리보기, 전체 parity 미검증 |\n| 04–14 | 미착수 | 모델 로드 자원 정책 등은 이후 이식 |

## 특별 주의
- `python/`은 기존 프로젝트 전체의 경로 변경이므로 그 내부 launcher, workflow, 상대 경로는 즉시 실행 가능하다고 가정하지 않는다.
- Rust 초안의 crate 이름과 턴 구분은 감사 결과에 따라 조정하되, 모든 기능 요구는 유지한다.
- 제거 대상인 MCP에만 접근 가능한 도구 기능은 **네이티브 대체를 구현한 뒤** 기능 보존 여부를 평가한다.

## 실제 구현 체크포인트 — 2026-11-14
- Rust workspace 및 `mai-core` 생성: `rust/Cargo.toml`, `rust/crates/mai-core`.
- 외부 의존 없는 fake-model 테스트 9개 작성: 직접 최종답변, lazy tool activation, 도구 결과/history, 미활성 도구 거부, 활성화 원자성, 사전/중간 취소, 빈 입력·빈 답변, 모델 실패, 중복 레지스트리.
- 기존 UI 소스 **미수정**, MCP/Verifier/mcp_memorizer 구현 없음.
- 이 구현은 agent core의 최소 **동기식** 스켈레톤으로, 실제 JSON typed schema/async timeouts/streaming/real llama.cpp/remote worker는 후속 구현해야 한다. 기존 계약 전체의 대체로 간주하지 않는다.
- 이 실행 환경에 cargo/rustc가 없어 테스트 **미실행**. `cargo test --workspace`, `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D warnings` 실행 필요.

## Turn 02 비동기 Core 확장 체크포인트 (2026-11-15)
- [x] 기존 Rust Core 테스트 9개 통과(사용자 Git Bash 로그).
- [x] `cargo fmt` 포맷 수정 및 GitHub commit 적용. CI에서 format/test/clippy 모두 통과.
- [x] `mai-runtime` Rust crate 도입: 비동기 모델 호출, JSON 도구 인자/부분 schema 검증, `tool_manual` 활성화, 각 호출 timeout, 취소, bounded 이벤트 채널.
- [x] 비동기 계약 테스트 7개 추가. Rust workspace 전체 GitHub Actions 실행 성공.
- [x] Web UI 코드 무변경 유지. MCP/Verifier/mcp_memorizer 도입 없음.
- [ ] `mai-core`/ `mai-runtime` 상태 머신 중복 통합, 완전 JSON schema 호환, active tool guard, 요청 전체 lifecycle/finalization, 실행 위치/원격 워커.
- [ ] 실제 llama.cpp / 웹서버 / UI 통합 및 PC Windows E2E.
- 다음 작업: 단일 정식 비동기 Core로 통합하고, 실제 HTTP API / UI 계약을 기존 코드와 비교해 구현.

## Turn 03 진행 기록 (2026-11-15)
- [x] 중복된 동기식 `mai-core` crate 제거, `mai-runtime` 단일 Agent FSM으로 통합.
- [x] `LlamaCpp` 실제 HTTP adapter: messages/tools, tool-call ID-결과 대응, 비정상/절단 응답 거부, fake TCP HTTP 서버 계약 테스트.
- [x] 새 `mai-web` crate: 기존 UI 정적 파일 *무변경* 제공, 로컬 전용 인증·chat job·history 기본 API 경로.
- [x] 이전 user/assistant 메시지의 최대 16개를 새 모델 요청에 첨부하도록 보완. 임의 tool-role 이력 삽입 거부.
- [x] `Cargo.lock`을 빌드 의존성 잠금으로 생성.
- [ ] User-visible API payload·음성·로그인/권한/영속 데이터의 전체 parity 확인 및 Windows E2E. 현재 `mai-web`은 보안·운영 기능이 완성되지 않은 로컬 미리보기.
- [ ] 실 모델 로딩/전환을 보장하는 Rust ModelSupervisor와 shared gateway 통합.
- [ ] 실제 Native Tools, remote worker, memory/Sentence_Breaker, benchmark 이식.
- [ ] 토큰 스트리밍과 실시간 thinking 이벤트, tool 결과 인자·에러형식 완전 보존.
- [ ] 기존 browser UI로 직접 로그인→대화→새로고침→취소까지 실행해 테스트.

## Turn 04 — Native ModelSupervisor 진행 (2026-11-15)
- [x] `mai-runtime::supervisor` 단일 상주 확인, unload → status 확인 → 사전 RAM/VRAM 검증 → load → status 확인 정책을 Rust로 작성.
- [x] 기본 RAM 6144 MiB, VRAM 최소 1024 MiB + 측정되지 않은 allocation 1024 MiB 여유 보존. 불확실한 측정치는 fail closed.
- [x] `mai-web` 실행 경로에 ModelSupervisor 전역 mutex를 연결, 모델 준비 및 Agent Tool loop 동안 동일 lock 소유.
- [x] Native Tools 첫 이식: `current_time`, `calculator`. Registry/JSON Schema 한 곳에서 선언해 별도의 MCP 인터페이스 없음.
- [x] mock router와 고정 RAM/GPU fixture를 이용한 계약 테스트 작성. 실제 모델 테스트는 미실시.
- [ ] 공용 llama.cpp gateway 통합/다중 프로세스 상주 제어, GPU 메타데이터 실측, 리소스 부족/모델 중단 오류 복구.
- [ ] calculator 원본 40-digit Decimal 및 모든 파싱 표현 동등성, 나머지 Native Tools 이식.
- [ ] Windows/Linux CI 및 실기 테스트 결과를 검증 후 최종 완료 여부 기록.

## Turn 04 모델 선택 API 확장
- [x] `GET /models` 모델 목록 및 파일명 후보 라벨 제공.
- [x] `POST /models/select` -> `ModelSupervisor.acquire`로 준비/상태 검증 후 선택 상태 변경.
- [x] 작업 접수 시 선택 모델 스냅샷 + 추론 종료까지 공통 gate 유지.
- [x] RunJob 실행 매개변수 하나로 통합해 Clippy의 과도한 인자 경고 제거.
- [x] 모델 선택 구현 Linux CI 형식·테스트·Clippy 통과.
- [ ] Windows CI/실제 GPU 모델 라우터 연동 별도 검증.


## Turn 05 파일/코드 도구 1차 진행 (2026-11-15)
- [x] `mai-runtime::workspace_tools` 단일 Registry 선언 구조 및 Workspace root 권한 경계.
- [x] `file_list`, `file_search`, `document_read`(UTF-8 text), `code_read`, `code_search`, `code_edit` 기본 동작 및 독립 Rust 단위 테스트.
- [x] 요청자가 명시적으로 `MAI_RUST_WORKSPACE_ROOT`를 설정한 로컬 preview에서만 노출.
- [x] 루트 밖 경로 차단, symlink 탐색 배제, 중복 편집 방지, 검색 결과가 잘렸음을 나타내는 collection 메타데이터.
- [x] Linux workspace Cargo test / Clippy 성공. Windows CI 및 실제 브라우저 사용은 별도 검증 중.
- [ ] 원본의 모든 파일 인코딩·구조화 문서·include/exclude globs·pagination 완전 parity.
- [ ] 파일 생성/복사/이동/삭제의 사용자 권한과 임시파일 추적·정리.
- [ ] Terminal의 하위 프로세스 종료/출력 크기 제한/timeout/cancel; Web의 SSRF/DNS/redirect 보호.
- [ ] Host/Client에 동일 ToolDescriptor를 전달하는 공통 dispatcher/worker 프로토콜.

## Turn 06 — 파일 변경과 실행별 scratch cleanup (2026-11-15)
- [x] `RunFiles` + `file_write/file_delete/file_move/file_copy` + 동일 범위의 `code_edit` 구현 및 `mai-web`의 작업별 등록.
- [x] `tmp/run-*` 실행별 디렉터리, 임시명 중복 거절, 사용자 persistent/existing 파일 보존, 다른 실행의 tmp 직접 수정/조회/검색 차단.
- [x] 종료 시 `spawn_blocking` 파일 변경과 단일 잠금으로 순서를 보장하고 작업별 scratch를 제거; 성공/실패/취소 모든 경로에서 적용.
- [x] 파일 조작·작업 격리·반복 cleanup·동시 조작과 cleanup의 경합 회귀 테스트 추가.
- [ ] 운영 수준 path symlink race/ACL·프로세스 crash recovery, 사용자/작업별 경계 추가 검토 및 실제 Windows 환경 E2E.
- [ ] Host/Client 원격 워커에 `native.cleanup` 신호를 보내는 통합 프로토콜과 공용 데이터 접근 권한.
- [ ] 전체 문서·네트워크·터미널 도구, 원본 Python의 모든 파일/인코딩 동작과 정밀 parity.
