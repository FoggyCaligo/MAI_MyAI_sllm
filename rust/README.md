# Rust Core 최소 구현

- 독립 Cargo workspace: `rust/Cargo.toml`
- Rust Core: `rust/crates/mai-core`
- 기존 Web UI: `python/mai/app/static/` **원본 내용 변경 없음**
- MCP, Verifier, mcp_memorizer: 구현하지 않음.

현재 범위는 mock Model을 이용하는 동기식 최소 상태 머신이다. 실제 모델 호출, HTTP API, JSON 입력 스키마, 비동기 timeout, 작업별 temp 정리, 메모리, worker, Sentence_Breaker, 벤치마크는 미구현이다. 기존 MAI에 대한 동작 동등성이 증명된 단계가 **아니다**.

```bash
cd rust
cargo test --workspace
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
```

이 환경에는 `cargo`/`rustc`가 없어 컴파일·테스트를 실행하지 못했다. 진행 기준은 `docs/blueprint/behavior-test-spec.md`를 참고.
