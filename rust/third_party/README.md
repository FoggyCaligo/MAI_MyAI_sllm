# 외부 코드 출처

## Sentence_Breaker

- 원본: https://github.com/FoggyCaligo/Sentence_Breaker
- 기준 커밋: `663dde2be708f4b44b2942d14baccff8330d37dd`
- 원본 파일: `src/sentence_breaker/lang_graph.py`
- 원본 라이선스: Apache-2.0. 전문은 `Sentence_Breaker-LICENSE`에 보존했다.
- Rust 이식: `crates/mai-runtime/src/sentence_breaker.rs`. 같은 레이어·위치의 경로 생존 수와 동적계획법 선택 기준을 유지하되, 경로 교집합을 점진 계산하고 호출자가 제공한 SQLite 트랜잭션에서 학습하도록 변경했다.
- 원본 스키마 및 원본 실행으로 생성한 합성 분절 비교 데이터는 `crates/mai-runtime/tests/fixtures/sentence-breaker-*`에 있다. 개인 데이터 및 원본 학습 DB는 포함하지 않는다.
