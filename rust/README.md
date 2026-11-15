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
