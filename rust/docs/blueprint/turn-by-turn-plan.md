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

## Turn 01 — 기능·호환성 감사
- 기존 `python/mai`, `python/portable`, `python/scripts`, `python/tests`, `python/benchmark` 전수 조사.
- API route/event schema, 옵션/환경변수, 모델/메모리 DB 스키마, 서드파티, 라이선스, 실효 timeout, 툴 목록, 프로세스 수명주기 표 작성.
- **유지/폐기/대체** 3분류 매트릭스 작성; MCP·Verifier·mcp_memorizer 및 관련 코드·문서 정리 대상 구체화. Sentence_Breaker 원본 알고리즘·라이선스 확보.
- 완료 기준: 모든 핵심 기능이 새 모듈과 최소 1개 검증 시나리오에 매핑됨.

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
| 01–14 | 미착수 | 각 턴에서 업데이트 |

## 특별 주의
- `python/`은 기존 프로젝트 전체의 경로 변경이므로 그 내부 launcher, workflow, 상대 경로는 즉시 실행 가능하다고 가정하지 않는다.
- Rust 초안의 crate 이름과 턴 구분은 감사 결과에 따라 조정하되, 모든 기능 요구는 유지한다.
- 제거 대상인 MCP에만 접근 가능한 도구 기능은 **네이티브 대체를 구현한 뒤** 기능 보존 여부를 평가한다.
