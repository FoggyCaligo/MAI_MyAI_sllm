# MAI Rust 재구성

기존 MAI 전체는 `../python/`에 보존. **기존 Web UI (`python/mai/app/static`)는 원본 그대로 유지**하며 Rust 런타임에 아직 연결하지 않았다.

## 현재 구현

- `mai-core`: 동기식 핵심 상태 머신과 mock model 계약 테스트 9개
- `mai-runtime`: Tokio 기반 비동기 실행 루프, JSON 도구 인자/일부 strict schema 검증, lazy `tool_manual` 활성화, bounded 이벤트 채널, 취소 및 모델/도구별 타임아웃; mock model 계약 테스트 7개
- Verifier / MCP / mcp_memorizer 미포함
- `cargo fmt --all -- --check`, `cargo test --workspace`, `cargo clippy --workspace --all-targets -- -D warnings` GitHub Actions 통과 (2026-11-15)

```bash
cd rust
cargo fmt --all -- --check
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
```

## 미구현·주의

실제 llama.cpp/클라우드 모델 연동, UI와 연결되는 HTTP/SSE/WebSocket 서버, 원격 워커, 비동기 임시 파일 수명주기, 영속 메모리, Sentence_Breaker, 벤치마크 이식은 아직 진행 전이다.

비동기 런타임은 `mai-core`에서 독립된 새 crate로 작성되어 있으므로, 다음 단계에서 중복 상태 머신을 정리하고 `mai-runtime`을 정식 Core로 채택한다. 현재 도구 schema 검증은 JSON Schema 일부(object, properties, required, enum, additionalProperties=false, 타입/배열)에 한정된다. API 서비스에 적용하기 전 전체 스키마 검증 도입 또는 정확한 제약 표현을 확정해야 한다.

이벤트 채널은 bounded이고 소비자가 처리하지 않으면 backpressure가 걸린다. 실제 서버 연결 때는 종료된 구독자·취소·느린 구독자에 대한 회복 전략이 필요하다. 타임아웃이 걸린 async future는 drop되지만 blocking 작업/외부 프로세스 강제 종료 및 정리는 별도 구현이 필요하다.
