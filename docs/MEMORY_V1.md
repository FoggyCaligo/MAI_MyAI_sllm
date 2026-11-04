# MAI 장기기억 V1 — 구조 및 동작 계약

> 기준: `FoggyCaligo/MAI_MyAI_sllm`의 `dev` (2026-11-04, `ef4833078349a3799881d2307b5d0e8b00164ee7` 확인).
> 이 문서는 과거 MACHI/MK 구현 없이도 현재 MAI의 장기기억 구조를 이해하고 재구성할 수 있도록 작성함.
> **현재 구현과 설계 목표를 구별함.** 메모리 검색은 모델이 필요할 때 명시적으로 호출함. 아래에 출처 보관·소유권 필터·시간적 우선순위의 실제 적용 범위와 한계를 구분함.

## 1. 핵심 원칙

MAI의 기억은 대화 문자열을 통째로 쌓아 놓는 단순 기록이나 임베딩 벡터 저장소가 아님. 원문 보관과 해석된 사실을 분리하는 **그래프 기반 기억**임. 현재 새 Fact에서 원문 Evidence로 가는 명시적 연결은 저장하지 않으므로, 원문 보관이 완전한 출처 역추적을 뜻하지는 않음.

- **원문 근거(Evidence):** 사용자의 실제 입력을 보존하는 별도 기록.
- **사용자 기준점(Anchor):** 한 `db_id`의 장기기억을 묶는 고정 노드.
- **사실(Fact):** 원문과 허용된 근거에서 추출한 재사용 가능한 서술.
- **개념(Concept):** Sentence_Breaker가 만든 canonical segment. 사실 검색을 위한 좌표.
- **연결(Edge):** 관계 종류와 출처를 가진 방향성 그래프 관계.

Sentence_Breaker는 Concept의 경계와 정체성을 결정하지만, 전체 기억 그래프를 대신하지 않음.

최종 답변 승인 후 비동기 추출을 수행하고, 저장 생략 대상이 아니면 사용자 원문을 `evidence`에 기록함. 추출한 Fact는 사용자 Anchor의 `asserted_fact`로, Fact를 분할한 Concept는 `mentions`로 연결함. 원문 Evidence와 새 Fact 사이에는 현재 명시적 연결이 없음.

같은 canonical segment는 같은 Concept 노드를 재사용함. 검색 점수가 비슷하다는 이유로 서로 다른 Concept의 정체성을 합치지 않음.

## 2. 영구 그래프 및 SQLite

현재 production이 사용하는 그래프의 핵심 노드 유형은 `anchor`, `fact`, `concept`임. 원문은 별도 `evidence` 테이블에 보존함. `utterance` 타입이 모델/이전 구조에 남아 있을 수 있지만, 현재 사용자 발화마다 새로운 그래프 Utterance 노드를 반드시 생성하는 계약은 아님.

주요 테이블은 다음과 같음.

| 저장 구조 | 기능 |
|---|---|
| `nodes` | 영구 노드 및 속성 |
| `user_anchors` | 사용자 고정 기준점 |
| `edges` | 유형이 있는 방향성 관계 및 출처 |
| `evidence` | 원문 근거 |
| `memory_concept_exact` | canonical Concept → 노드 ID 정확 매핑 |
| `memory_concept_fts` | SQLite FTS5 어휘 기반 후보 검색 |

사용자 기준점의 `identity_key`는 `anchor:user:<memory_user_id>`, 표시 본문은 `user_anchor::<memory_user_id>`임. 현재 계정 설정에서 영구 저장소를 식별하는 값은 로그인 `user_id`가 아니라 **`db_id`**임.

현재 주요 관계는 다음과 같음.

```text
user_anchor → fact     : asserted_fact
fact        → concept  : mentions
```

관계는 모델이 자연어로 임의 생성한 의미를 그대로 실행하지 않고 런타임에서 정한 관계 유형을 사용함. 출처에는 `user_utterance`, `user_assertion`, `derived_from_utterance`, `fact_index` 등의 값이 존재할 수 있음. 데이터베이스는 `(from_node_id, to_node_id, relation)`의 중복 관계 생성을 방지함.

## 3. 입력과 기록의 분리

원문 사용자의 발화, 모델이 추론한 Fact, 검색 결과에서 관찰한 정보는 동일한 출처가 아님.

1. 최종 응답이 승인된 뒤 FactExtractor가 Fact 후보를 추출함.
2. `{"facts": [string, ...]}` 구조와 비어 있지 않은 문장을 검사하고 저장 생략 조건을 판단함. 별도 출처 번호 검사는 없음.
3. 생략 대상이 아니면 실제 사용자 원문을 Evidence에 보관하고 Fact와 Concept를 저장함.
4. 추출 실패는 경고로 드러내고 원문 보관을 시도하며, 원문을 거짓 Fact로 대체하지 않음.

현재 production에서는 FactExtractor를 **선택된 chat model과 같은 모델**로 구성하며 `think=False`를 사용함. 별도 `MEMORY_MODEL`을 요구하지 않음.

### 3.1 기억 조회로 기억을 재증폭하지 않음

통합 도구 `memory_search`로 읽어 온 과거 기억을 새로 확인된 사용자 진술이나 외부 세계 사실처럼 다시 저장하지 않음.

기억 질문만 있었고 추출에 성공했으며 새 Fact가 없다면 persistent write를 생략할 수 있음. 다만 같은 사용자 메시지가 새로운 직접 진술을 포함하는 경우에는 검색 도구 사용 자체가 새 Fact 저장을 막지 않음.

## 4. 실제 production 처리 순서

```text
사용자 요청
  → 계정/db_id 및 최근 대화 확인
  → 같은 사용자의 이전 비동기 memory write 완료 대기
  → 요청별 WorkingGraph 생성
  → native tool catalog / tool_manual
  → 필요 시 memory tool 활성화 및 호출
  → main agent가 임시 답변 구성
  → final grounding verifier 검사
       ├─ reject: 메인 agent에 최근 교정 피드백만 전달하고 계속 실행
       └─ accept: 최종 답변 공개
  → background FactExtractor / MemoryRuntime.finish_turn
```

장기기억 전체를 매 요청마다 자동으로 모델 prompt에 삽입하지 않음. 필요 여부는 main agent가 판단함. 기본 경로에서는 별도의 사전 tool-requirement planner를 실행하지 않고, `tool_manual`로 native schema를 활성화함. 현재 코드에는 사전 planner나 required-tool 집합을 고정하는 별도 설정 경로가 없음.

사용자별 직전 기억 후처리가 진행 중이면 다음 요청은 해당 작업을 기다림. 따라서 직전 턴의 수정사항을 아직 쓰지 않은 상태에서 다음 기억 조회를 시작하지 않도록 순서를 보장함.

## 5. ConceptIndex: 정확 일치와 FTS5

기본 검색 좌표는 모델 임베딩이 아닌 `SqliteFtsConceptIndex`임.

```text
Fact 저장 → Sentence_Breaker → Concept 노드 및 색인
모델의 recall query → 공백 분리 → exact hash / FTS5 → Concept 후보 → Fact
```

- **Exact:** canonical Concept text와 영구 Concept ID를 정확 매칭하며 실행 시 사전으로 읽어 들임.
- **FTS5:** Exact로 충분한 좌표를 찾지 못할 때 어휘 기반으로 후보를 제공함.
- **동일성:** 검색 결과가 Concept를 합치거나 기존 그래프 ID를 바꾸지 않음.
- **기존 데이터:** 이전 sqlite-vec 테이블이 남아 있더라도 조용히 삭제하지 않고 현재 검색에서는 사용하지 않음.
- **색인 정합:** SQLite 시작 시 영구 Concept 노드 중 색인 누락 항목을 비파괴적으로 동기화함.

검색은 임베딩 의미 유사도가 아니라 lexical match임. 따라서 표현이 다른 질문은 검색어를 다시 구성해야 할 수 있음.

## 6. 모델에 노출되는 기억 도구

모델에는 `memory_search` 하나를 등록하며, 인자로 세 모드를 구분함. `memory_recall`과 `memory_overview`는 별도 native tool 이름이 아님.

| 호출 | 모드와 입력 계약 |
|---|---|
| `memory_search(keyword="만년필", limit=5)` | 주제 검색. `limit`은 선택이며 양수, 최종 Fact 수에 적용 |
| `memory_search(limit=8)` | 최근 목록. `limit`을 반드시 명시하며 1~50 |
| `memory_search(node_id=123)` | 알려진 노드의 one-hop 확장. `keyword`·`limit`과 함께 지정 불가 |

### 6.1 주제 검색

1. 검색어를 공백으로 나누고 중복 chunk를 제거함. 검색 시 Sentence_Breaker를 다시 호출하지 않음.
2. chunk별 Concept 후보를 최대 12개 조회하고 중복 후보는 높은 점수를 유지함. 전체 seed도 기본 12개로 제한함.
3. seed의 one-hop에서 현재 사용자 소유 Fact를 모음. Fact 점수는 연결된 seed의 최고 점수이며 동점은 안정적인 발견 순서임.
4. 선택적 `limit`은 최종 Fact 순위에 적용함. 생략하면 일치 Fact 전체가 대상임.
5. 현재 사용자 anchor까지 경로가 있는 Fact로 작업 그래프를 구성함. 별도의 기본 anchor Fact 목록은 덧붙이지 않음.

### 6.2 최근 목록과 확장

최근 목록은 현재 anchor에 `spoke` 또는 `asserted_fact`로 직접 연결된 항목을 `last_seen_at DESC, id DESC`로 읽음. 예전 Utterance도 있을 수 있으며 전체 기억을 보장하지 않음. 호출 시 개수 기본값은 없음.

노드 확장은 one-hop과 현재 anchor까지의 경로를 작업 그래프에 합침. 현재 사용자 anchor 자체를 지정하면 기본 5개 Fact 문맥을 반환하고, 다른 사용자 anchor는 거부함. 일반 노드 확장 전체에 사용자 소유권 필터가 적용되는 것은 아님.

### 6.3 Working Graph

작업 그래프는 요청 동안 여러 조회 결과를 내부적으로 누적하지만, 매 호출은 해당 조회 결과를 반환함. `model_snapshot()`에는 Fact/Utterance의 `node_id`, `node_type`, `data`, `created_at`, `provenance`, `evidence_id`만 포함함. Concept와 내부 연결 구조는 모델 출력에서 제외함. 새 Fact의 `evidence_id`는 현재 보통 비어 있음.

내부 `MemoryRuntime.explicit_recall`, `memory_overview`, `memory_search` 메서드는 남아 있으므로 native tool 이름과 내부 함수 이름을 구분해야 함.

## 7. 출력량과 페이지 크기

아래 값은 현재 배포 설정과 도구 입력 모델 기준임.

| 항목 | 현재 값 | 비고 |
|---|---:|---|
| 주제 검색 Concept seed | 12 | chunk별 후보와 전체 seed의 기본 상한 |
| anchor 확장 Fact 수 | 5 | 최근 목록 모드와 별개 |
| 최근 목록 Fact 수 | 명시적 1~50 | 도구 호출 기본값 없음 |
| `web_search` 결과 수 | 10 | 명시적 최대 15 |
| `web_search` snippet 문자 수 | 160 | 검색 결과별 |
| `web_fetch` 기본 본문 문자 수 | 33,333 | 명시적 `max_chars` 가능 |
| `MAX_INLINE_TOOL_RESULT_CHARS` | 8,000 | `settings.yaml`; 직접 `MAIRuntime` 구성 시 환경변수 누락 기본값은 10,666 |

도구 결과는 크기와 관계없이 요청별 원문 저장소에 보관함. 본 모델은 최초 bounded page를 읽고 `tool_manual.retain_evidence`로 필요한 원문 범위를 선택함. 다음 문맥과 factual verifier에는 선택 발췌·참조를 주며, 원문은 `tool_result_read` 또는 verifier의 구조화된 재열람으로 확인함. 원문 삭제나 별도 요약 LLM 호출은 없음.

기억 추출은 이 최종 발췌가 아니라 성공한 비기억조회 실행의 `context_content`(최초 model-visible view)를 사용함. 현재 제외 기준은 성공한 `memory_search`이며, 성공한 `tool_manual`·`tool_result_read`는 이 단계에서 별도로 제외하지 않음. 실패 실행은 제외함. 상세 계약은 [도구 결과 전달](TOOL_RESULT_PAGINATION.md)과 [검증](verifier.md)을 참고함.

## 8. 시간적 우선순위 및 충돌

과거 기억은 현재 사용자 발화나 이번 요청의 실제 도구 결과보다 우선하지 않음.

동일한 subject/property/time scope에 대해 여러 Fact가 서로 충돌하면 `created_at`이 최신인 Fact를 우선함. `occurrence_count`, retrieval rank, tool 호출 순서, `last_seen_at`으로 새 기록의 우선권을 대신하지 않음.

다만 서로 다른 역사적 시점에 대한 Fact는 단순히 나중에 기록됐다는 이유로 삭제하지 않음. 시각이 같거나 출처를 비교할 수 없다면 충돌을 드러내며 확실하지 않은 우선순위를 만들어 내지 않음.

해당 우선순위는 구조화된 model context와 verifier policy에도 전달됨. 단, 과거 정정 기록을 물리적으로 자동 삭제하거나 모든 표현의 의미 충돌을 확정적으로 해결하는 전용 correction engine이 완성됐다는 의미는 아님.

## 9. 검증·실패 계약

- user/tool evidence와 과거 memory를 구분함.
- 검색 결과가 비어 있더라도 “기억이 없음”의 확정 근거로 바꾸지 않음.
- 잘못된 `limit`, unknown node, 다른 사용자 anchor 접근은 오류로 드러냄.
- SQLite FTS5 불능이나 Concept identity 충돌을 임의의 검색 방식으로 성공 처리하지 않음.
- 실패한 Fact 추출을 성공으로 꾸미지 않고 원문 근거를 보존함.
- model은 기억의 주제·관계·사람 정체성을 문자열 포함 규칙으로 판별하지 않음.
- verifier가 거절한 임시 답변은 Fact 근거가 아님.
- 감사 로그 `log.sqlite3`는 기억과 분리되며 model에 자동 주입하지 않음.

## 10. 구현 코드 및 테스트

| 범위 | 경로 |
|---|---|
| 그래프 표/관계 | `mai/memory/graph/schema.py`, `repository.py` |
| Fact 및 원문 보존 | `mai/memory/runtime.py`, `extraction/service.py` |
| ConceptIndex | `mai/memory/index/sqlite_fts.py` |
| Sentence_Breaker 연동 | `mai/memory/segmenter.py` |
| Recall 및 Fact 순위 | `mai/memory/recall/service.py` |
| Working Graph | `mai/memory/working.py` |
| 모델 노출 memory tools | `mai/memory/tools.py` |
| 백그라운드 후처리 | `mai/app/runtime.py` |
| tool 반복 및 verifier | `mai/agent/guards.py`, `mai/agent/loop.py` |

관련 테스트에는 `test_recall_ranked_limit.py`, `test_memory_temporal_precedence_policy.py`, `test_memory_runtime.py`, `test_memory_admission.py` 등이 있음.

**핵심:** MAI 기억 시스템은 **원문 근거를 보존하면서, 모델이 명시적으로 검색하는 그래프 기반 장기기억**임. 시간적 우선순위를 모델에 지시하고 검색 문맥은 필요한 만큼만 노출함. 원문과 Fact의 직접 연결 및 일반 그래프 확장의 소유권 필터에는 위에 적은 한계가 있음.
