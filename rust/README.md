# MAI Rust 재구성

이 저장소는 기존 MAI를 **Rust 네이티브 런타임으로 단계적으로 이식 중**입니다. 기존 전체 프로젝트는 `../python/`에 보존되어 있으며, Web UI는 `../python/mai/app/static/`의 **원본 파일을 그대로 사용합니다**.

## 현재 Rust workspace

- `crates/mai-runtime`: 정식 비동기 Agent Core. 지연 도구 활성화(`tool_manual`), JSON 인자 검증의 제한된 부분집합, CancellationToken, model/tool timeout, 이벤트 채널, 채팅 히스토리 입력.
- `crates/mai-runtime/src/llamacpp.rs`: 외부 llama.cpp router의 OpenAI-compatible `/v1/chat/completions` 클라이언트. tool call ID 및 결과 대응, 잘못된 JSON·절단 응답 거부. **현재는 비스트리밍 요청**.
- `crates/mai-web`: **127.0.0.1 로컬 전용 개발 미리보기**. 기존 UI를 읽어 제공하고 preauth login, bearer session, `/models`, `/chat/jobs`, `/chat/jobs/active`, `/chat/jobs/{id}`, `/session/{id}`의 첫 호환 경로를 제공.
- 중복 동기식 `mai-core` crate는 제거. 초기 구현은 Git 이력에서 복구 가능.
- MCP·Verifier·mcp_memorizer는 새 Rust 구현에 포함하지 않습니다.

## 테스트

```bash
cd "$(git rev-parse --show-toplevel)/rust"
cargo fmt --all -- --check
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
```

`Cargo.lock`을 Git에 추적합니다.

## 로컬 HTTP 미리보기 (실서비스에 사용하지 말 것)

실제 MAI와 포트가 겹치지 않도록 **기본 포트 8100**을 사용합니다. Rust 서버는 코드상 loopback `127.0.0.1`에만 바인딩하므로 Tailscale Funnel / Cloudflare / 외부 프록시로 노출하지 마세요. 기존 `OWNER_USERS` 값과 실제 로드된 llama.cpp 모델 ID가 필요합니다. Rust 서버는 아직 `.env`를 자동 로드하지 않습니다.

Git Bash 예제 (계정과 모델 값은 본인 환경에 맞춰 설정):

```bash
cd "$(git rev-parse --show-toplevel)/rust"
export OWNER_USERS='[{"user_id":"test-owner","user_pw":"test-only-password","db_id":"test-db"}]'
export MAI_RUST_MODEL='실제로_로드된_모델_ID'
export LLAMACPP_HOST='http://127.0.0.1:8080'
cargo run -p mai-web
```

브라우저에서 `http://127.0.0.1:8100/`을 열 수 있으나, **기존 UI 기능 전체가 작동한다고 보장하지 않습니다**. 임시 테스트용 자격증명을 사용하고, 실제 데이터를 이 서버로 옮기지 마세요.

## 아직 지원하지 않는 동작

- llama.cpp 모델 자동 다운로드와 llama.cpp 프로세스 실행/갱신은 미구현. 단일 상주/모델 선택과 RAM/VRAM 사전 검증은 Rust ModelSupervisor에 첫 구현이 있지만, 공유 게이트웨이 및 실제 GPU 검증은 아직 완료되지 않았습니다.
- Host/Client 원격 워커, 전체 Native Tools 및 tool_manual 카탈로그, 반복 도구 실패 가드, 임시파일 및 작업별 cleanup.
- 기존 DB에 저장된 채팅/장기기억, trial 계정, 로그인 보안 정책 전체, 업로드/이미지, 다운로드 링크, STT/TTS·음성 API, cloud model providers.
- 전체 실시간 토큰 스트리밍·thinking 진행·API payload 동등성. 현재의 작업 진행 이벤트는 부분 호환입니다.
- Windows 실제 환경 통합시험 및 benchmark, Sentence_Breaker Rust 이식.

이 버전의 `mai-web`은 **메모리 내 세션/채팅/작업 상태**만 유지합니다. 서버를 재시작하면 사라지며, 원본 Python SQLite 데이터는 접근하지 않습니다. 프로덕션 대체 전 데이터·인증·자원 보호를 완성해야 합니다.

공식 구현 기준선: `docs/blueprint/behavior-test-spec.md`, `docs/blueprint/core-unification.md`.

## Turn 04 — ModelSupervisor와 첫 Native Tools (2026-11-15)

- `mai-runtime::supervisor`: 시스템 가용 RAM 측정(`sysinfo`), 기본 최소 RAM 6144 MiB 및 GPU 잔여 1024 MiB 정책. 라우터의 `/models`, `/models/unload`, `/models/load`를 이용하여 **기존 모델이 완전히 unloaded 상태가 된 후** 다음 모델을 로드. 모델당 GPU memory metadata에서 weight/context/compute 및 추가 1GiB overhead를 확인한다. 상태·측정치를 알 수 없으면 새 로딩을 거부.
- `mai-web` 작업 실행은 `ModelSupervisor::acquire`로 **하나의 추론 실행 동안 잠금을 유지**. 실제 Tool loop까지 잠금 범위에 포함한다.
- `mai-runtime::native_tools`: 원본과 같은 이름의 `calculator`, `current_time`을 MCP 없이 Rust 네이티브로 등록. 나머지 Tool은 아직 미구현.
- Rust 계산기는 Python의 `decimal` 40자리 컨텍스트와 달리 `rust_decimal` 정밀도 한계가 있으며, 현재 지수 표기 등의 입력도 모두 지원하지 않는다. **전체 calculator 동등성을 주장하지 않는다**.
- 모델 잠금은 **이 Rust 프로세스 내부에서만** 유효하다. Python MAI/OpenClaw 등 다른 프로세스가 같은 llama.cpp 라우터에 접근할 경우 기존의 공유 메모리 게이트웨이 또는 차후 Rust 통합 게이트웨이가 별도로 필요하다. 이 보호 없이 동시에 사용하지 않는다.
- ModelSupervisor는 llama.cpp 프로세스를 직접 시작하거나 GGUF 다운로드/설정을 변경하지 않는다. 개발용 preview에서도 기존 라우터가 이미 실행 중이어야 한다.
- 실기 GPU VRAM 측정 및 Windows RX6800XT 환경 시험은 별도 필요하다.

### 모델 선택 경로
- `GET /models`는 라우터에 등록된 모델 목록과 현재 선택 모델을 반환한다.
- `POST /models/select`는 동일 Rust 프로세스의 `ModelSupervisor` 잠금 안에서 안전한 모델 교대를 수행하고, 성공했을 때에만 선택 값을 반영한다.
- 채팅 Job은 **접수 시점의 선택 모델**을 스냅샷하여 추론에 사용한다. 진행 중 다른 모델로 바뀌어도 해당 작업은 자신의 모델을 명시적으로 준비한다.
- 모델 잠금은 프로세스 내부에서만 동작하므로, **기존 Python MAI 또는 OpenClaw와 같은 라우터를 동시에 쓰는 환경에서는 단일 상주 보호가 보장되지 않는다.** 향후 공유 게이트웨이 이식 전에 병용하지 않는다.


## Turn 05 — 파일·코드 Native Tools 1차 (2026-11-15)

- `mai-runtime::workspace_tools`에 공통 `Workspace` 경계와 `Registry` 등록을 추가. **MCP 전송·중복 ToolRegistry·Host/Client용 별도 래퍼를 사용하지 않는다.**
- `file_list`: 파일/디렉터리 조회, 재귀/디렉터리만/최대 개수, `collection.complete` 등 페이지 완료 정보.
- `file_search`: 이름과 상대 경로에 대한 glob 검색, 최대 결과 개수.
- `document_read`: **일반 UTF-8 텍스트 전용** (기존 Python의 PDF/DOCX/XLSX/PPTX/CSV 구조화 파싱 미이식).
- `code_read`: 명시적 파일의 줄 번호와 범위에 맞춘 읽기.
- `code_search`: UTF-8 내용 검색, literal/regex 모드, 결과 수 제한. 기존 포함/제외 glob, 파일 크기·줄 범위 상세 옵션은 아직 이식 전.
- `code_edit`: 기존 파일에서 정확히 한 번 나타나는 리터럴 문자열만 변경. 없거나 여러 번 나타나면 수정 거절. 같은 디렉터리에서 임시 파일에 쓰고 파일을 교체한다.
- `mai-web`에는 `MAI_RUST_WORKSPACE_ROOT` 환경 변수를 명시한 **개발용** 실행에서만 위 6개 도구를 추가한다. 미설정 시 기존처럼 `calculator`, `current_time`만 사용한다.
- 경로는 루트 기준 정규화하고 작업 루트 밖 접근을 차단한다. 탐색 중 symlink는 따라가지 않는다. **이 제한은 기존 owner 계정의 'OS가 허용한 모든 경로' 동작과 다르며, 완전 이식이 아닌 개발용 방어 경계**다.
- `code_edit`은 아직 원본 파일의 메타데이터/권한 완전 보존, 동시 수정 감지, 강제 종료 중 복구를 검증하지 않았다. 작업자별 분리·인증·스코프 정책도 추후 구현해야 한다.
- `file_write/file_delete/file_move/file_copy`는 Turn 06에서 실행별 보호를 갖춘 개발용 구현을 추가했다. `code_symbols`, 구조화 문서 편집, `web_search/web_fetch`, `terminal_run`은 여전히 미구현. 특히 원격/호스트 `./tmp` 수명주기와 터미널 프로세스 트리 정리를 먼저 설계해야 한다.

개발용 opt-in 예시: `export MAI_RUST_WORKSPACE_ROOT="/path/to/test-workspace"` (반드시 실데이터가 아닌 테스트 경로 사용).

## Turn 06 — 실행별 파일 변경·임시파일 정리 (2026-11-15)

- `mai-runtime::file_mutations`에 `file_write`, `file_delete`, `file_move`, `file_copy`, 실행별 `code_edit`를 추가. 기존 `workspace_tools`의 읽기·검색 기능과 **동일한 입력 이름**을 쓰되 파일 변경은 매 작업의 `RunFiles`로만 등록.
- `file_write(lifecycle="temporary")`는 요청된 **안전한 상대 경로**를 `<workspace>/tmp/run-<무작위>/...`에 매핑하고 실제 경로를 반환한다. 이 경로는 같은 실행의 후속 파일 조회에서 재사용 가능하다. 기존 파일을 임시파일로 덮어쓰거나 자동 삭제 대상으로 등록하지 않는다.
- `file_write(lifecycle="persistent")`와 기존 파일을 덮어쓴 경우는 작업 종료 후에도 유지된다. `file_move/file_copy`는 현재 일반 파일만 지원하며 목적지 덮어쓰기를 거부한다. 임시 파일을 작업 디렉터리 밖으로 **이동**하는 것은 거부한다. `file_delete`는 작업/워크스페이스 최상위 루트를 삭제하지 못한다.
- 파일 변경을 하는 `spawn_blocking` 작업과 최종 정리는 하나의 실행별 mutex를 공유한다. 정리를 시작하면 해당 실행은 `closed` 상태로 전환되어 나중에 시작된 작업이 쓰기를 수행하지 않는다. 정리는 성공/실패/취소 경로에서 **사용자에게 성공을 보고하기 전에** 실행별 임시 디렉터리를 제거한다. 정리 실패는 작업의 오류 상태에 반영한다.
- 읽기·파일검색·코드검색도 실행별로 제한되어 다른 작업의 `tmp/run-*` 경로를 직접 읽거나 루트 재귀 검색으로 열람하지 못한다.
- 설계는 기존 **Python 원본/웹 UI 무변경**, 개발용 로컬 `mai-web` 옵션 `MAI_RUST_WORKSPACE_ROOT`를 유지한다.

**남은 제한:** 운영용 동시 파일 수정 감지, 강제 프로세스 종료 시 보장되는 정리, 실행 간 공용 저장소 ACL, NTFS/ACL/하드링크·심볼릭 링크를 포함한 모든 TOCTOU, 교차 파일시스템 이동·디렉터리 복사, 원격 워커 cleanup 통신, 실제 llama.cpp·UI E2E는 아직 검증하지 않았다. `spawn_blocking` 작업이 이미 시작된 후 취소되면 해당 작업이 마무리된 뒤 정리가 이루어지며, **이미 완료된 영구 파일 변경을 자동으로 롤백하지 않는다.**
