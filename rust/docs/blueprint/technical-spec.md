# MAI Rust 재구성 기술 명세서
> 상태: 초기 청사진 (2026-11-14) · 기준: `recovery/flag-mcp-20261114` / `c43010dfa2d1b9bc672623fbf02d2eb2544f4fc8`
> 구현 전 코드 감사로 API·데이터 스키마·기존 동작을 확정한다. 이 문서는 새 설계의 요구사항이지 현재 구현 완료 목록이 아니다.

## 1. 목표와 범위
- 기존 MAI의 사용자 관찰 가능 기능을 유지하면서 핵심 런타임을 Rust로 재구현한다.
- **폐기**: Verifier 실행/모델 교대, MCP 프로토콜·서버·클라이언트 계층, `mcp_memorizer` 의존 및 연동. 해당 기능의 필수 동작은 Rust 네이티브 인터페이스로 재설계하되 MCP 호환성은 목표가 아니다.
- **유지**: 단일 Main 모델 중심의 llama.cpp GGUF 라우터, 모델 선택·로딩·언로딩, 1 resident 정책, VRAM/RAM 보호, 대화·에이전트 실행, Native Tools, 파일·문서·검색·브라우저·터미널, 영속 메모리, Host/Client 원격 워커, 스트리밍, 취소·타임아웃, 인증·호스팅, 진단·로그, 패키징·업데이트, 기존 Web UI 및 STT/TTS.
- **Rust 전면 이식**: 사용자가 고안한 Sentence_Breaker의 기능과 검증 사례. 알고리즘의 지적재산·라이선스 표기를 보존하고, 기존 구현과 출력 동등성 테스트를 한다.
- **벤치마크**: MAI 측 Python 어댑터·러너·채점·보고를 가능한 한 Rust로 이식한다. 공식 GAIA/LongMemEval 배포 자료, 제3자 평가기 또는 외부 에이전트별 Python/JS 필수 요소는 독립적으로 조사한 뒤 호환성 경계를 기록한다. 외부 공식 평가 결과와 비교 가능한 채점 규칙을 함부로 수정하지 않는다.
- **Web UI**: 기존 프런트엔드 소스·화면·동작 보존. Rust 서버가 기존 HTTP/WebSocket/SSE 계약을 구현한다. 프런트엔드 프레임워크 전환은 범위 밖이다.

## 2. 디렉터리
```text
/
├── python/                     # 현 시점의 모든 Git 추적 파일 보관 (기존 구조 그대로)
└── rust/
    ├── docs/blueprint/
    │   ├── technical-spec.md
    │   └── turn-by-turn-plan.md
    ├── Cargo.toml               # 구현 턴에서 추가할 workspace
    ├── crates/                  # 구현 턴에서 추가
    │   ├── mai-core/            # 실행 상태 머신, 정책, 취소
    │   ├── mai-server/          # 웹 UI 호환 API 및 인증
    │   ├── mai-model/           # llama.cpp router client, 모델/자원 관리
    │   ├── mai-tools/           # native tools, tool_manual, 결과·임시파일
    │   ├── mai-worker/          # Host/Client 연결 및 작업 실행
    │   ├── mai-memory/          # SQLite, FTS, 검색, 그래프, 작업 메모리
    │   ├── sentence-breaker/    # 알고리즘의 순수 Rust 구현
    │   ├── mai-speech/          # STT/TTS 인터페이스·브라우저 연동
    │   └── mai-bench/           # 벤치마크 CLI/어댑터
    └── web/                     # 기존 UI를 내용 유지하여 이관할 목적지
```
현재는 **이동과 문서만** 수행한다. crate 및 UI 이관은 검증 가능한 후속 턴에서 진행한다. `python/`은 언어가 Python인 파일만이 아니라 종전 프로젝트 전체의 동결된 스냅샷을 뜻한다. `python/LICENSE`와 `python/NOTICE` 포함 원본을 유지한다. 향후 루트 LICENSE/NOTICE, README, 실행 진입점은 별도 검토해 생성한다.

## 3. 시스템 구조와 계약
```text
기존 Web UI ─ HTTP/WebSocket/SSE ─ Rust mai-server
                                      │
                                      ▼
                             mai-core (Agent FSM)
                             ├─ mai-model ─ HTTP ─ llama.cpp router
                             ├─ mai-tools ── Native tools
                             ├─ mai-memory ─ SQLite/FTS
                             ├─ sentence-breaker
                             └─ mai-worker ─ Host/Client protocol ─ remote worker
```
- LLM 자체는 검증된 `llama-server` 외부 프로세스로 유지한다. Rust 바인딩으로의 교체는 요구사항이 아니다. 기존 `mai/llm/cloud_api.py`가 제공하는 OpenAI/Anthropic/OpenRouter 외부 모델 경로도 유지한다.
- `Agent FSM` 단계: 요청 수락 → 환경/모델 확정 → 컨텍스트 구성 → 모델 스트리밍 → 필요 시 도구 수행 → 결과 반영 → 최종 응답/취소/실패. **임의의 에이전트 라운드 상한은 두지 않는다**. 사용자 취소·시간 제한·자원 제한은 별도 정책으로 적용한다.
- 도구 시스템: MCP 없이 Rust native registry + typed input/output schema + `tool_manual`. 도구 설명은 사용 전 필요할 때 노출하며, boolean 검색·툴 조건·Host/Client 실행 위치를 명확히 한다. OS와 무관하게 기본 쉘은 Git Bash/bash 정책을 존중하되 실제 설치·호환성 검증이 필요하다.
- Host/Client는 실행 환경의 용어다. Client 선택 후 추가 전환 전까지 연속적인 도구 실행이 선택된 Client 환경에서 이뤄져야 한다. 기본 Host로 몰래 복귀하지 않는다. 원격 연결 실패와 작업 중단은 명시적으로 보고한다.
- 도구별 timeout, 전체 요청 timeout, 모델 호출 timeout을 혼동하지 않는다. 기존 `settings.yaml`과 실행 코드를 조사해 **실효값과 적용 범위**를 명세·테스트로 확정한다. 임의 기본값을 새로 만들지 않는다.
- Worker 임시파일은 해당 worker의 `./tmp` 내부에 생성한다. 최종 호출 종료 신호 수신 시 안전하게 비우되, 동시 작업간 소유권·중복 신호·연결 단절·프로세스 강제 종료를 구분한다. 사용자가 보존하도록 지정한 파일이나 워커 작업 디렉터리 외부 파일을 삭제하지 않는다.
- 모델 정책: 공유 `LLAMACPP_MEMORY_GUARD_ENABLED` 정책과 외부 클라이언트까지 포함한 게이트웨이 소유권을 보존하고, 별도 lock 중복 여부는 감사·검증 후 통합한다. 한 번에 하나의 GGUF 상주, 모델 전환시 언로드·재로드 직렬화, 시작 전 VRAM 여유 검사, RAM 예산 검사, 실패 시 기존 상태 명확히 표시. 기존 모델 목록과 Q8 기본 설정은 실제 기준 브랜치·현재 설정 간 차이가 있을 수 있으므로 구현 전 검증한다.
- 메모리는 MAI 자체 Rust 구현을 사용한다. 기존 사용자 데이터·SQLite 스키마·검색 품질을 보존하도록 읽기·마이그레이션을 분리하고 데이터 손상 방지 백업 및 역마이그레이션 경로를 설계한다. `mcp_memorizer`를 호출하지 않는다.
- 사용자-facing 이벤트: 현재 UI가 기대하는 메시지/모델명/진행상태/툴 결과/에러/취소/음성 인터페이스의 payload와 이벤트 순서를 캡처하여 계약 테스트한다.
- 비동기 동작: Tokio 기반 cancellation-safe task 관리, 구조화 로깅(tracing), bounded queues, backpressure, 타임아웃 및 프로세스 재시작/정리의 소유권 모델을 정의한다.
- 보안: 네트워크 노출시 인증, 경로 검증 및 작업권한 경계, 프로세스 argument injection 차단, 작업별 로컬/원격 경계, 민감 로그 마스킹, localhost llama.cpp router의 비공개 유지.

## 4. 라이브러리 후보 (고정 전 검증)
- Tokio, Axum, Serde, reqwest, tracing, clap, thiserror/anyhow.
- SQLite는 `sqlx` 또는 `rusqlite` 중 실제 동시성 요구와 마이그레이션/FTS 지원을 검토하여 택한다.
- Rust Cargo workspace + Windows/Linux 빌드·테스트 자동화. 실제 의존 버전·라이선스·보안 적합성은 채택 시 확인한다.
- 기존 Web UI 빌드 툴은 소스 변경 없이 유지한다.

## 5. 성능 및 신뢰성 검증
1. 동일 PC·모델·컨텍스트·GPU 설정에서 Python MAI와 Rust MAI A/B 비교: 유휴·최대 RSS, 모델 로드, TTFT, 토큰 속도, 반복 툴 실행, CPU, 종료 후 리소스.
2. 작업 취소(수동/음성), 모델 전환 중 취소, 원격 단절, 재연결, 다중 작업, 10시간 미사용 종료, 타임아웃·프로세스 크래시, VRAM/RAM 부족 시나리오.
3. 기존 테스트의 핵심 회귀 시나리오 이관; 특이 동작은 golden traces 및 fixture로 기록한다.
4. Sentence_Breaker: 한국어·다국어·인용·약어·개행·스트리밍 분할 사례의 기존 출력과 비교, 의도된 차이는 별도 승인.
5. 벤치마크: GAIA/LongMemEval의 기존 데이터셋 버전, split, scoring 조건, timeout, 외부 에이전트 실행 조건을 기록; Rust 구현과 공식 기준의 결과를 대조한다.
6. 전환 기준: 기능 체크리스트 충족, 복구 가능, 고장 재현 테스트 통과, 기존 UI 계약 유지, 측정 결과 문서화. 기능 누락을 성능 향상으로 상쇄하지 않는다.

## 6. 비목표·리스크·결정 보류
- Python MAI를 당장 삭제하거나 `dev`/`main`을 덮어쓰지 않는다.
- 기존 UI를 재작성하지 않는다.
- MCP/Verifier/mcp_memorizer를 Rust로 포팅하지 않는다. 단, 이들에 묶여 있던 **필수 사용자 기능**은 별도 네이티브 경로로 복구한다.
- Rust 컴파일 성공 ≠ 동작 보장: 외부 시스템, 네트워크, 데드락, 논리·프로토콜 버그는 통합·실기 테스트가 필요하다.
- 현시점에서 실제 변경 후 기존 진입점·CI가 깨지는 것은 예상된다. 참조용 `python/`과 개발용 `rust/`의 별도 실행/빌드 경로를 후속 턴에서 구축한다.

## 7. Turn 01 정적 감사 반영
- [`legacy-audit.md`](legacy-audit.md): 전체 파일 목록과 주요 기능/API/설정 확인, 근거 수준 및 실기검증 대기항목.
- [`behavior-test-spec.md`](behavior-test-spec.md): Rust에서 재현할 기능·실패·보안·원격·메모리·모델·벤치마크 계약.
- [`consolidation-opportunities.md`](consolidation-opportunities.md): 기능 보존 조건 아래 통합할 과다 인터페이스 및 유지할 독립 경계.
- [`legacy-file-inventory.json`](legacy-file-inventory.json): 원본 376개 추적 파일의 SHA/크기/도메인 매니페스트.
- **확인된 Web UI 실체는 정적 HTML/JS** (`python/mai/app/static/`). Vue 기반이라는 가정은 사용하지 않는다. UI 자체 변경 금지.
- 실기 golden trace, 모든 함수 레벨 호출 관계, Sentence_Breaker 외부 소스 감사는 아직 수행 전이다. 완전한 동작 동등성 보증으로 표현하지 않는다.
