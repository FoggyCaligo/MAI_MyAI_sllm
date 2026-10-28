# MAI 장기기억 V1 — 구조 및 동작 계약

> 기준: `FoggyCaligo/MAI_MyAI_sllm`의 `dev` (2026-10-28 확인).
> 이 문서는 과거 MACHI/MK 구현 없이도 현재 MAI의 장기기억 구조를 이해하고 재구성할 수 있도록 작성함.
> **현재 구현과 설계 목표를 구별함.** 메모리 검색은 모델이 필요할 때 명시적으로 호출하며, 기억의 출처·소유권·시간적 우선순위를 유지함.

## 1. 핵심 원칙

MAI의 기억은 대화 문자열을 통째로 쌓아 놓는 단순 기록이나 임베딩 벡터 저장소가 아님. 원본 발화와 해석된 사실을 분리하는 **근거 보존형 그래프**임.

- **원문 근거(Evidence):** 사용자의 실제 입력을 보존하는 별도 기록.
- **사용자 기준점(Anchor):** 한 `db_id`의 장기기억을 묶는 고정 노드.
- **사실(Fact):** 원문과 허용된 근거에서 추출한 재사용 가능한 서술.
- **개념(Concept):** Sentence_Breaker가 만든 canonical segment. 사실 검색을 위한 좌표.
- **연결(Edge):** 관계 종류와 출처를 가진 방향성 그래프 관계.

Sentence_Breaker는 Concept의 경계와 정체성을 결정하지만, 전체 기억 그래프를 대신하지 않음.

```text
사용자 원문 → immutable Evidence
                  ↓ 최종 답변 승인 후 비동기 추출
             Fact ← asserted_fact ─ User Anchor
               ↓ mentions
             Concept
               ↓
     exact hash / SQLite FTS5 색인
```

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

사용자 기준점은 `user_anchor::<memory_user_id>` 형태의 영구적인 정체성을 가짐. 현재 계정 설정에서 영구 저장소를 식별하는 값은 로그인 `user_id`가 아니라 **`db_id`**임.

현재 주요 관계는 다음과 같음.

```text
user_anchor → fact     : asserted_fact
fact        → concept  : mentions
```

관계는 모델이 자연어로 임의 생성한 의미를 그대로 실행하지 않고 런타임에서 정한 관계 유형을 사용함. 출처에는 `user_utterance`, `user_assertion`, `derived_from_utterance`, `fact_index` 등의 값이 존재할 수 있음. 데이터베이스는 `(from_node_id, to_node_id, relation)`의 중복 관계 생성을 방지함.

## 3. 입력과 기록의 분리

원문 사용자의 발화, 모델이 추론한 Fact, 검색 결과에서 관찰한 정보는 동일한 출처가 아님.

1. 실제 사용자의 문장을 원문 근거로 보존함.
2. 최종 응답이 승인된 뒤 FactExtractor가 Fact 후보를 추출함.
3. 출처와 구조를 검사한 뒤 Fact와 Concept를 저장함.
4. Fact를 만들지 못했더라도 원문을 거짓 Fact로 대체하지 않음.

현재 production에서는 FactExtractor를 **선택된 chat model과 같은 모델**로 구성하며 `think=False`를 사용함. 별도 `MEMORY_MODEL`을 요구하지 않음.

### 3.1 기억 조회로 기억을 재증폭하지 않음

`memory_recall`, `memory_overview`, `memory_search`로 읽어 온 과거 기억을 새로 확인된 사용자 진술이나 외부 세계 사실처럼 다시 저장하지 않음.

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

장기기억 전체를 매 요청마다 자동으로 모델 prompt에 삽입하지 않음. 필요 여부는 main agent가 판단함. 기본 경로에서는 별도의 사전 tool-requirement planner를 실행하지 않고, `tool_manual`로 native schema를 활성화함. `TOOL_REQUIREMENT_VERIFIER=true`를 켜면 선택적으로 별도 required-tool 검증을 사용할 수 있음.

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

### 6.1 `memory_recall(query, limit=None)`

특정 주제에 대한 Fact 검색 도구임.

현재 `RecallService` 동작:

1. 모델이 작성한 검색 문자열을 **공백 기준 chunk**로 나누며 Sentence_Breaker를 다시 호출하지 않음.
2. 각 chunk에 대해 FTS5/Exact Concept 후보를 최대 `concept_limit`개 조회함.
3. 중복 Concept는 높은 검색 점수를 유지하고, 전체 seed를 점수순으로 정렬·제한함. 현재 기본 `concept_limit=12`.
4. 각 Concept의 one-hop에서 해당 사용자 Fact만 모음.
5. Fact 점수는 연결된 seed의 최고 점수로 계산하며, 안정적인 순서로 정렬함.
6. **`limit`이 지정되면 최종 Fact 수에 적용함.** 생략 시 일치 Fact를 전부 반환함.
7. 사용자 anchor로 가는 경로가 있는 Fact만 Working Graph에 추가함.
8. 모델에는 Fact 중심 snapshot을 반환하며 Concept 검색 노드는 내부 좌표로 유지함.

`memory_recall(query="만년필", limit=5)`는 Concept 후보 5개가 아니라 관련성 높은 Fact 최대 5개를 뜻함.

### 6.2 `memory_overview(limit=8)`

검색어 없이 사용자에게 직접 근거가 있는 최근 사실을 넓게 훑는 도구임. 기본 `limit=8`이며 입력 허용 범위는 1~50임. 기본 출력량은 이전의 12개에서 8개로 축소했음. 명시적으로 더 큰 limit을 지정하는 기능은 유지함.

### 6.3 `memory_search(node_id)`

영구 그래프의 특정 노드를 **한 단계(one-hop)** 확장하고 이번 요청의 Working Graph에 합침. 임의 깊이의 그래프 탐색을 몰래 실행하지 않음.

현재 사용자 anchor를 지정하면 특례로 bounded Fact context를 반환함. 기본 anchor context는 8개에서 **5개**로 축소함. 다른 사용자의 anchor 확장은 거부함.

### 6.4 Working Graph

Working Graph는 사용자 요청 동안만 살아 있는 임시 상태임. 여러 번의 조회·확장 결과를 내부적으로 합칠 수 있지만, 매 호출에서 누적 전체 그래프를 다시 출력하지 않고 해당 호출의 결과를 돌려줌.

모델에게 노출하는 기억은 내용 중심의 Fact/Utterance와 해당 노드 ID 및 출처 정보에 한정함. Concept 노드 및 내부 topology는 검색 좌표로 활용하되 일반 `memory_recall` 결과에 전부 싣지 않음.

## 7. 출력량과 페이지 크기

메모리 및 웹 도구에서 모델 문맥 부담을 줄이기 위해 이번 변경은 기본 노출량을 약 2/3로 줄임.

| 항목 | 기존 기본값 | 변경 기본값 | 비고 |
|---|---:|---:|---|
| `memory_overview` Fact 수 | 12 | 8 | explicit limit 가능 |
| `memory_search`의 anchor Fact 수 | 8 | 5 | 기존 대비 약 63% |
| `web_search` 결과 수 | 15 | 10 | provider 조회 정책 유지 |
| `web_search` snippet 문자 수 | 240 | 160 | 검색 결과별 |
| `web_fetch` 기본 본문 문자 수 | 50,000 | 33,333 | explicit max_chars 가능 |
| `MAX_INLINE_TOOL_RESULT_CHARS` | 16,000 | 10,666 | 원문은 별도로 보존 |

**중요:** `memory_recall(limit=None)`은 전체 일치 Fact를 반환한다는 기존 계약을 유지함. 전역에서 일치 Fact를 2/3만 잘라내지 않음. 필요한 경우 모델이 명시적으로 `limit`을 선택해야 함.

큰 도구 출력은 `ToolResultStore`에 원문을 보관하고 `tool_result_read(result_id, offset, limit)`로 이어 읽음. 표기된 수치는 모델에 한 번에 노출되는 기본 출력량의 조절이며, 영구 데이터 삭제나 근거 절단을 의미하지 않음.

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

**핵심:** MAI 기억 시스템은 **원문 근거를 보존하면서, 모델이 명시적으로 검색하는 그래프 기반 장기기억**임. Fact의 출처와 시간적 우선순위를 지키며 검색 문맥은 필요한 만큼만 모델에 노출함.
