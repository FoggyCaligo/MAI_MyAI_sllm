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
- 기존 DB의 채팅 이력, 장기기억의 전체 Working Graph/경로 병합, trial 계정, 로그인 보안 정책 전체, 업로드/이미지, 다운로드 링크, STT/TTS·음성 API, cloud model providers.
- 전체 실시간 토큰 스트리밍·thinking 진행·API payload 동등성. 현재의 작업 진행 이벤트는 부분 호환입니다.
- Windows 실제 환경 통합시험 및 benchmark. Sentence_Breaker 분절·학습은 Turn 14에서 Rust로 이식했으며 실제 설치 DB를 사용한 검증은 남아 있다.

이 버전의 `mai-web`은 **메모리 내 세션/채팅/작업 상태**를 유지하므로 서버를 재시작하면 해당 상태가 사라집니다. 장기기억 SQLite 접근은 명시적인 경로 설정이 필요하며 기본은 읽기 전용입니다. Turn 14의 개발용 쓰기 설정을 켜면 장기기억 후처리를 수행합니다. 프로덕션 대체 전 데이터·인증·자원 보호를 완성해야 합니다.

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

## Turn 07 — 선택적 Native Terminal 첫 구현 (2026-11-15)

- `mai-runtime::terminal`에 `terminal_run`을 구현했다. **Bash가 기본**이며, Windows에서는 `shell=cmd`, `shell=powershell`도 선택할 수 있다. Linux는 PATH의 Bash를 사용한다. Windows는 Git for Windows의 `bin/bash.exe`만 명시적으로 찾아 실행하며, 자동 검색이 안 되면 `MAI_GIT_BASH_PATH`에 실행 파일 경로를 지정한다.
- `mai-web`에서는 `MAI_RUST_ENABLE_TERMINAL=1` **및** `MAI_RUST_WORKSPACE_ROOT`를 둘 다 명시해야 도구를 등록한다. 미설정 상태에서는 **터미널이 활성화되지 않는다.** Rust 서버 자체는 loopback(127.0.0.1) 전용으로 유지된다.
- 명령어·cwd·shell·temporary_outputs JSON 인자를 사용하고 stdout/stderr/종료 코드를 분리해 반환한다. 성공하지 않은 종료 코드는 도구 오류로 보고한다. 합산 출력 기본 한도 1 MiB, 명령 타임아웃 기본 180초. 시간 초과·출력 초과·취소 시 프로세스 트리 종료를 시도한다.
- Unix는 별도 프로세스 그룹을 만들고 그룹 종료 시그널을 전달한다. Windows는 `taskkill.exe /PID ... /T /F`와 부모 프로세스 강제 종료를 조합한다. 둘 다 **best effort**이며, 권한 분리·데몬화된 자식이나 OS에서 별도 작업 그룹으로 분리된 프로세스까지 종료된다는 보장은 없다. Windows Job Object 전환을 운영 전 검토해야 한다.
- PowerShell은 사용자 스크립트를 별도 `*.ps1`로 만들어 자식 PowerShell에 `-ExecutionPolicy Bypass -File`로 전달한다. 기존 Python의 UTF-16LE EncodedCommand와 구현 방식이 다르므로 실제 Windows 인코딩·인자 전달 및 CP949 등 레거시 출력 디코딩 parity는 미완성이다.
- `temporary_outputs`는 현재 실행의 scratch 경로를 cwd로 선택한 경우에만 허용하며, 기존 파일·중복 파일·경로 이탈을 거부한다. 실행별 `RunFiles` 정리 시 현재 작업의 파일만 제거한다. 터미널은 **샌드박스가 아니며** 쉘 안에서 별도의 절대 경로로 이동·쓰기 등을 수행할 수 있으므로, 신뢰하는 소유자만 사용하는 로컬 개발 환경 외에는 활성화하면 안 된다.
- 남은 작업: Windows 프로세스 트리/Job Object·Windows 로케일·중첩 PowerShell 계약 완전 parity, 셸 권한 샌드박싱, remote Host/Client dispatch, 테스트 시 실제 프로젝트 및 데이터 보호. 검증 환경에서 사용하더라도 실데이터가 아닌 테스트 workspace를 사용할 것.

예시(기본 비활성 상태에서 명시적으로 켤 때만):
```bash
export MAI_RUST_WORKSPACE_ROOT="/path/to/disposable-test-workspace"
export MAI_RUST_ENABLE_TERMINAL=1
cargo run -p mai-web
```

- Turn 07 당시 단발 터미널 설명을 대체한다. Turn 08부터 Bash는 작업별 지속 세션을 유지한다. CMD/PowerShell은 여전히 단발 실행이다.
- Windows에서 PATH의 `bash`가 WSL 런처와 충돌하는 문제를 해결했으며, Windows Git Bash/CMD/PowerShell 직접 실행 테스트를 추가했다.

## Turn 08 — Owner 기본 터미널과 지속 Bash 세션

- Rust 미리보기 서버의 `OWNER_USERS` 인증 계정은 현재 모두 `owner` 역할이다. **Owner 터미널 기본값은 활성화**다. `MAI_RUST_ENABLE_TERMINAL=0` (또는 `false`/`off`)를 지정하면 비활성화한다.
- `MAI_RUST_WORKSPACE_ROOT`가 지정되어 있지 않으면 **서버 시작 프로세스의 현재 디렉터리**를 초기 작업 루트로 사용한다. 터미널은 OS 사용자 권한으로 명령을 실행하는 것이므로 **샌드박스가 아니다.** 지금의 Rust 서버는 로컬 loopback 전용 미리보기이며, 공개 인터넷 연결은 금지한다.
- Bash: `terminal_run(command="cd subdir")` 다음 `terminal_run(command="pwd")` 호출에서 같은 셸의 cwd와 환경변수가 유지된다. `session_id` 기본값은 `default`; 최대 **Host 2개** 세션을 동시에 가진다.
- `terminal_run(command="exit",session_id="default")`로 해당 세션을 닫고 슬롯을 재사용한다. 에이전트의 한 작업이 종료·실패·취소되면 **모든 해당 작업의 셸을 닫고 난 뒤** `tmp/run-*`을 정리한다.
- 세션은 **한 에이전트 답변의 여러 도구 호출 동안만** 유지한다. Turn 09부터 실험적 Rust Client 워커에도 2개의 지속 Bash 세션이 있다(Host 2 + Client 2/작업).
- **현재 지속 세션은 Bash만 지원**한다. Windows CMD/PowerShell은 이전처럼 호출별 단발 프로세스로 작동한다. 지속 세션에서는 `cwd` 인자를 지정하지 않고 `cd`를 사용한다. `temporary_outputs`가 필요한 경우 `file_write(lifecycle=temporary)`를 대신 사용한다.
- Bash 명령의 비정상 종료 코드는 명시적인 도구 오류지만 **셸은 그대로 유지**된다. 취소·타임아웃·출력 한도 초과·실제 프로토콜 오류가 발생하면 해당 셸을 강제 종료한다.
- Windows 실제 실행 환경의 인코딩, 프로세스 하위 트리 강제 종료, Windows Job Object, Host/Client 원격 실행은 추가 검증·개발이 필요하다. 기존 Web UI / Python 코드는 변경하지 않았다.

## Turn 09 — Rust Host/Client Native 워커 (2026-11-15)

- Rust에 작업별 `switch_workspace(target="host"|"client")`를 이식했다. 기본 대상은 host, 동일 대상을 연속 선택하면 `changed=false`이다. Client 전환은 해당 Owner의 인증된 워커가 연결되어 있을 때에만 확정되며, Client 연결 실패 시 Host fallback은 금지된다.
- Rust Web API `POST /remote/session`은 인증된 Owner에게 임시 원격 세션 및 비밀키를 발급한다. `DELETE /remote/session/{id}`는 Owner가 자신의 세션을 폐기한다. 워커는 `X-MAI-Agent-Secret`을 포함한 `/remote/agent/{id}/poll` 및 `/result`로 요청·결과를 교환한다. 기존 Python 워커의 `native.invoke`, `native.cleanup` 계약과 같은 필드 구조를 사용한다.
- Rust Client 실행 파일: `cargo run -p mai-web --bin mai-worker`. 환경변수 `MAI_REMOTE_SERVER`(HTTPS 필수), `MAI_REMOTE_SESSION`, `MAI_REMOTE_KEY`, `MAI_CLIENT_WORKSPACE_ROOT`를 반드시 지정한다. 클라이언트가 HTTPS로 외부 연결하므로 인바운드 포트는 열지 않는다.
- 워커는 파일·코드 및 터미널 Native 도구를 실행하며 작업별 `RunFiles`와 `TerminalSessions`를 생성한다. Host Bash 최대 2세션 + Rust Client Bash 최대 2세션으로 작업별 4세션 구조다. 양측 모두 `exit`과 최종 cleanup에서 세션을 종료한다. 원격 run_id는 기존 Python 워커 규칙과 같은 32자리 소문자 16진수다.
- Client로 한 번이라도 전환한 작업은 종료 시 `native.cleanup` 확인이 필수다. 워커의 `cleaned=true`가 확인되지 않으면 정상 완료로 처리하지 않는다. 작업별 임시 파일만 정리하고 영구 파일은 보존한다.
- **운영 제한**: Rust Web은 계속 `127.0.0.1`에만 수신하므로 외부 워커 연결에는 별도의 안전한 HTTPS 리버스 프록시 또는 터널이 필요하다. Python의 원격 연결 설치 UI를 Rust에 아직 이식하지 않았으며, 세션 생성은 Owner 인증 API를 통한 수동 절차다.
- **Turn 09 기준 취소 제한(과거 기록)**: 당시 워커는 요청을 순차 처리해 실행 중인 명령을 취소할 수 없었다. Turn 10에서 `native.cancel`을 분리된 제어 요청으로 처리하도록 변경했다. 여전히 Windows Job Object, 오프라인 상태의 강제 종료 보장, 실기기 E2E는 남아 있다.

### 실험적 수동 연결

1. Rust Web 서버에 Owner 인증으로 `POST /remote/session`을 호출해 받은 `session_id`와 `secret`을 안전하게 보관한다.
2. 별도 Client PC에서 위 네 환경변수를 설정한 뒤 `cargo run -p mai-web --bin mai-worker`를 실행한다. `MAI_REMOTE_SERVER`는 인증서가 검증되는 HTTPS 주소여야 한다.
3. 작업 제출 시 `/chat/jobs`의 `remote_session_id`에 세션 ID를 넣고 모델이 Native `switch_workspace(target="client")`로 전환하면 파일·코드·터미널 도구가 Client에서 실행된다.
4. 세션이 끊겼거나 재발급해야 하면 Owner 인증으로 `DELETE /remote/session/{id}`를 호출한다.

## Turn 10 — 원격 실행 중 취소와 연결 종료 정리 (2026-11-16)

- Rust Host는 사용자 작업이 취소되면 `native.cancel` 신호를 **`native.cleanup`보다 먼저** Client로 보낸다. 원격 취소 ACK는 12초 이내에 받아야 하며, 확인되지 않으면 작업의 오류 메시지에 표시한다.
- Rust Client는 긴 `native.invoke`를 실행하는 동안에도 다른 HTTP 폴링을 계속 처리한다. `native.cancel`은 실행별 `CancellationToken`을 즉시 취소하여 지속 Bash 명령의 취소 경로를 작동시킨다. 동일 run의 파일·코드 명령은 직렬화되어, 실행 중인 블로킹 파일 작업이 완료된 뒤에 cleanup이 진행된다.
- `native.cleanup`은 실행 중인 작업과 동기화해 해당 실행의 Bash 세션과 `tmp/run-*` 디렉터리를 정리한다. 정리 후 도착한 동일 run의 명령은 거부한다.
- 워커 폴링 연결 오류, 세션 폐기, 결과 송신 실패, Ctrl+C 발생 시 활성 실행에 취소 신호를 보내고 자식 셸을 종료한 후 작업별 임시 디렉터리를 정리하도록 했다.
- 원격 결과 중단과 취소 요청은 서로 독립된 요청으로 전달한다. 연결이 끊긴 경우 서버가 즉시 취소 성공을 **보장하지 않고** ACK 부재를 오류로 보고한다. Client가 네트워크 장애를 감지한 후 자체 정리하는 것은 best-effort이며, 강제 종료(SIGKILL/전원 차단/OS crash)는 Rust 코드가 보장할 수 없다.
- 파일 변경은 취소 시 롤백되지 않는다. Windows 프로세스 트리 종료 방식은 현재 `taskkill /T /F` 기반의 best-effort이며, Job Object 기반 강제 종료 보증과 서버·노트북 간 실환경 E2E는 남아 있다.
- 신규 테스트: 원격 실행 대기 중 독립 취소 전달, Client 장시간 명령 취소, 연결 종료 정리, 영구 사용자 파일 보존, 원격 라우트의 취소→정리 순서.


## Turn 11 — Client 재연결·중복 요청 방지·비정상 종료 복구 (2026-11-16)

- Client 임시파일은 Host와 분리된 `<MAI_CLIENT_WORKSPACE_ROOT>/tmp/client-worker/run-XXXXXX`에 생성한다. 다른 경로, 특히 Host의 `tmp/run-*`는 워커 복구 대상이 아니다.
- Client 워커는 시작 시 `tmp/client-worker/.worker.lock`에 OS 배타 파일 잠금을 획득하며, 동시에 두 워커가 같은 Client scratch를 정리하지 못하게 한다. 잠금을 얻은 뒤 정해진 `run-XXXXXX` 형태의 **이전 실행에서 남은 디렉터리**만 제거한다. 일반 파일·다른 이름의 디렉터리·심볼릭 링크는 유지한다. 워커가 잠금을 보유하는 동안 새 작업이 생긴다.
- 원격 요청은 `request_id`를 키로 삼는다. 동일한 ID/내용을 중복 수신하면 메모리의 같은 결과를 반환하고, 같은 ID에 다른 내용이 오면 거부한다. Host 역시 동일한 결과 응답의 중복 ACK는 허용하지만 서로 다른 내용의 재전송은 거부한다.
- Client는 요청을 실행하기 **전에** `tmp/client-worker/.request-ids` 파일에 ID를 추가하고 `sync_data()`를 완료한다. 워커가 재시작하면 이전에 기록된 ID는 실행하지 않고 명시적으로 거부한다. 이는 **재시작 후 결과를 자동 복원하는 기능이 아니며**, 완료됐는지 알 수 없는 작업을 재실행하지 않는 *fail-closed/at-most-once* 정책이다.
- 안전한 자동 재연결은 완료되지 않은 실행도 미전송 결과 작업도 없는 **유휴 상태**에서만 최대 3회 재시도한다. 활성 작업 중 통신 오류는 재실행·Host 대체·조용한 성공 처리 없이 취소 및 정리 경로로 이동한다.
- 요청 기록 파일이 잘렸거나 손상된 경우 워커는 실행을 거절한다. 기록은 최대 100,000개 이전 ID까지 관리하고, 문제 발생 시 해당 파일의 상태를 확인한 후 수동으로 조치해야 한다. 임의 삭제하면 과거 요청의 재실행 방어 기록이 사라진다.
- 파일 정리는 실행별 임시 디렉터리만 대상으로 하며, 이전에 완료된 **영구 파일 변경이나 이미 실행된 외부 명령의 부작용은 롤백하지 않는다.** 전원 강제 차단·OS crash 후 남은 자식 프로세스 종료, Windows Job Object, 실제 HTTPS PC-to-PC E2E, 부팅 중 symlink 교체 경쟁 조건 등은 추가 검증이 필요하다.
- *참고:* 이 단계는 Rust Client 워커에 구현한 복구 로직이다. Python 원격 워커의 자동 정리 정책이나 Web UI는 변경하지 않았다.


## Turn 12 — 기존 Memory SQLite 읽기 전용 검색 (2026-11-17)

- Rust Web에서 `MAI_RUST_MEMORY_DB_PATH`가 설정된 경우에만 기존 Python의 `MAI_MEMORY/memory.sqlite3` 파일을 **읽기 전용으로 연다**. DB가 없거나 스키마가 다르면 오류가 발생하며 생성·마이그레이션·수정하지 않는다.
- Native `memory_search`를 인증된 Owner의 **로그인 user_id**에만 묶어서 등록한다. `query` 또는 `keyword`(동시에 사용 불가)로 `AND / OR / NOT`, `& | !`, 괄호, 인용구를 검색한다. `node_id`는 해당 사용자의 Fact/Utterance 또는 자신의 anchor만 확장한다. `limit` 단독 호출은 최근 기억의 제한된 개요를 제공한다.
- Concept 정확 일치와 기존 SQLite FTS5 인덱스의 어휘 검색을 지원한다. 정확 일치가 먼저이며, FTS5 후보는 BM25와 Concept ID로 순위를 정한다. 검색은 Boolean 조건과 계정 소유권을 적용한 뒤 결과 개수 제한을 적용한다. FTS5 테이블이 없는 이전 DB에서는 정확 일치 검색을 사용한다.
- Concept 주변의 전체 경로 병합, 추출·저장·작업 중 Working Graph, 최종답변 후 사실 저장은 이식 전이다.
- Owner DB 식별자가 로그인 `user_id`와 다른 설치에서는 별도의 ID 매핑이 필요하다. 다른 사용자의 Fact, Concept node_id는 직접 조회할 수 없다.
- 환경변수 설정 예: `MAI_RUST_MEMORY_DB_PATH=./MAI_MEMORY/memory.sqlite3` (서버의 실행 작업 디렉터리 기준). 실제 SQLite 파일을 사용하므로 **개인 DB 파일은 공개 Git 저장소에 절대 커밋하지 않는다**.

## Turn 13 — 메모리 턴 단위 저장 어댑터 (2026-11-21)

- `native_memory_write::FactWriter::save_turn`은 이미 저장이 승인된 사용자 원문과 `AdmittedFact` 목록을 받아 Evidence, Fact, Concept, 관계 및 정확/FTS5 인덱스를 **하나의 SQLite 트랜잭션**으로 저장한다. 후속 Fact 저장이 실패하면 원문 증거와 앞서 변경한 기존 노드의 출현 횟수까지 롤백한다.
- Python의 실제 `_postprocess_memory` / `finish_turn` 흐름을 기준으로 한다. 원문은 `user_utterance` Evidence에 그대로 보존하고, 현재 Python에서 생성하지 않는 Utterance 그래프 노드는 추가하지 않는다. 같은 턴의 동일 Fact는 한 번만 반영하고 다른 사용자의 동일 Fact는 별도 노드로 유지한다. 공유 Concept은 재사용한다.
- Fact가 없는 승인된 턴도 원문 증거를 보존할 수 있다. **새 Fact가 없는 회상 전용 턴의 제외 여부는 호출자가 판단해야 한다.** 이 어댑터는 사실 추출·승인 정책을 대신하지 않는다.
- 기존 Python JSON의 공백·한글 이스케이프에 관계없이 사용자 소유권을 비교한다. 기존 Fact의 `created_at` 및 payload를 보존하고 정확 인덱스의 ID/문자열 충돌을 양방향으로 검사한다. DB 생성·마이그레이션·사용자 anchor 자동 생성은 하지 않는다.
- 테스트는 Python 원본의 그래프 스키마를 직접 읽어 CHECK·외래키가 적용된 임시 DB를 사용한다. 여러 Fact 저장 후 검색, 사용자 격리, 원문 보존, 중복 방지 및 전체 롤백을 검증한다.
- **Turn 13 당시에는 독립 어댑터 단계였다.** 당시 Web의 `MAI_RUST_MEMORY_DB_PATH`는 계속 읽기 전용이고 자동 저장이나 모델의 `memory_save` 도구는 등록하지 않는다. Sentence_Breaker, 비동기 사실 추출, 승인 정책, 계정 ID 매핑 및 Web 작업 완료 경로 연동은 다음 단계다. 원본 Python 코드와 Web UI는 변경하지 않았다.


## Turn 14 — 사실 추출·Sentence_Breaker·답변 후 저장 연결 (2026-11-21)

- Python과 같은 추출 프롬프트로 별도 llama.cpp 구조화 JSON 요청을 보낸다. 현재 사용자 메시지, 이전 assistant 메시지, 최종답변의 역할을 구분하고 **성공한 비회상 도구의 실제 모델 노출 결과**만 새 증거로 전달한다. 추출기는 도구를 실행하지 않는다. 잘못된 JSON·추가 필드·빈 사실·절단 응답을 거부한다.
- 성공한 `memory_search`가 있고 새 사실이 없으면 해당 턴을 저장하지 않는다. 새 사실이 있는 혼합 턴은 저장한다. 추출 실패/90초 시간 초과는 원문 Evidence를 보존하며 `memory_update.extraction_error`에 드러낸다. 취소는 원문 저장 승인으로 바꾸지 않는다.
- Sentence_Breaker의 문자 경로·레이어 생존 수·동적계획법과 동점 규칙을 Rust로 옮겼다. 원본 `663dde2b`에서 생성한 **131개 순차 입력**과 결과가 일치한다. 별도 DB의 기존 스키마를 사용하며 사전/공백 휴리스틱으로 대체하지 않는다. 원본 출처와 라이선스는 `third_party/README.md`에 기록했다.
- 명시적으로 저장을 켠 경우에만 답변과 작업 완료 상태를 먼저 공개하고 메모리 후처리를 진행한다. 같은 계정의 다음 추론은 이전 저장 완료를 기다린다. 추출도 ModelSupervisor 잠금을 사용한다. 후처리 실패는 이미 생성한 답변을 실패로 바꾸지 않고 작업 조회의 `memory_update`에 표시한다.
- 검색·저장의 소유권은 로그인 이름인 `user_id`가 아니라 **Python과 같은 안정적인 `db_id`**다. 사용자 ID 중복, DB ID 중복 및 다른 계정과의 식별자 충돌을 시작 시 거부한다. 기존 이름을 추측하거나 자동 이관하지 않는다.

### 개발용 자동 저장 설정

기본값은 계속 읽기 전용이다. 자동 저장을 시험하려면 기존 DB의 테스트용 복사본을 준비하고 아래 세 값을 명시한다. 각 `OWNER_USERS.db_id`의 anchor가 복사한 메모리 DB에 있어야 한다. DB 생성·스키마 마이그레이션·anchor 자동 생성은 하지 않는다.

```bash
export MAI_RUST_MEMORY_DB_PATH='/path/to/test-memory.sqlite3'
export MAI_RUST_SEGMENT_DB_PATH='/path/to/test-sentence_breaker.sqlite3'
export MAI_RUST_MEMORY_WRITE=1
```

기존 Sentence_Breaker DB를 지정하며 원본 학습 DB를 이 저장소에 추가하지 않는다. 저장 플래그가 없거나 `0`/`false`/`off`이면 추출 요청 및 저장을 수행하지 않는다. 잘못된 플래그 값이나 저장에 필요한 경로 누락은 시작 오류다.

**트랜잭션 범위:** 메모리 DB에 분절 DB를 `ATTACH`하고 원문, 사실, 인덱스, 문자 경로 학습을 함께 커밋한다. 일반 오류·감지된 취소는 두 DB 모두 롤백한다. SQLite의 제약상 WAL 모드에서 전원 차단/OS crash 시 두 파일의 동시 원자 커밋까지 보장하지는 않는다. 기존 Python 프로세스가 동일 DB를 열어 둔 상태의 인덱스 캐시 동기화도 미검증이므로 이 단계는 분리한 테스트 복사본을 대상으로 한다.

**남은 검증:** 실제 모델의 사실 추출 품질, 실제 Windows/학습 DB 통합, 전체 Working Graph/자동 회상 동등성, 강제 종료 중 복구 및 성능 측정. 기존 Python 실행 코드와 Web UI는 수정하지 않았다.
