# MAI Native 장기기억 엔진과 검색

복구 브랜치 `recovery/flag-mcp-20261114`는 `flag_native_tool` 시점의
`mai/memory/` 그래프·색인·추출·저장 구조와 요청별 `WorkingGraph`를 복원한다.
검색 알고리즘에는 독립 [MCP_memorizer](https://github.com/FoggyCaligo/MCP_memorizer)의
Boolean 검색 및 Fact/Utterance 검색 확장을 가져왔다. **MCP 서버를 실행하지 않는다.**

## 소유 및 저장 경계

- 그래프, SQLite 스키마, FTS5 개념 색인: `mai/memory/graph/`, `mai/memory/index/`
- Fact 추출 프롬프트 및 LlamaCppAdapter 연동: `mai/memory/extraction/service.py`
- Recall 및 Boolean 파서: `mai/memory/recall/service.py`, `query.py`
- 도구 등록과 모델 메모리 우선순위 정책: `mai/memory/tools.py`
- 답변 확정 후 근거 선택·비동기 저장 승인: `mai/memory/admission.py`, `mai/app/runtime.py`
- 분리 알고리즘: 외부 [Sentence_Breaker](https://github.com/FoggyCaligo/Sentence_Breaker).
  MAI Native 어댑터는 `mai/memory/segmenter.py`에 있다.
- 실제 DB 기본 경로: 프로젝트 루트 `MAI_MEMORY/memory.sqlite3`;
  Sentence_Breaker 데이터는 `MAI_MEMORY/sentence_breaker.sqlite3`를 사용한다.

## 모델에 노출되는 검색 도구

오직 **Native `memory_search` 한 개**를 `ToolRegistry`에 등록한다.
첫 모델 차례에서 `tool_manual`로 활성화한 뒤 스키마를 확인한다.

| 입력 | 동작 |
|---|---|
| `query="토마토"` | 키워드 Concept 검색 |
| `query="토마토 AND NOT 방울토마토"` | Boolean 검색. `AND`/`OR`/`NOT`, `&`/`|`/`!`, 괄호와 따옴표 지원 |
| `keyword="토마토"` | 과거 Native 호출과의 호환 입력 (`query`와 동시 사용 금지) |
| `node_id=123` | 이미 반환된 사용자 소유 노드 확장 |
| `limit=10` | 제한된 최근 기억 개요 (1~50) |
| `query="토마토", limit=5` | 검색 결과 상위 5개 |

개선된 검색은 저장된 Fact와 Utterance 노드 모두를 조회할 수 있다.
다만 `flag_native_tool`의 기존 저장 규칙은 복구했기 때문에 새 발화를
무조건 Utterance 노드로 생성한다는 뜻은 아니다. 이전 DB에 Utterance 노드가
있다면 검색 대상으로 포함된다. 검색은 FTS5/문자열 기반이며 의미 임베딩
또는 동의어 자동 확장은 아니다.

## 수명과 보안

매 요청마다 새 `WorkingGraph`를 만들고 같은 요청의 Native `memory_search`
호출들이 이를 공유한다. 새로운 사용자 요청에는 이 임시 그래프를 계승하지 않는다.
SQLite 장기기억은 사용자 `memory_user_id` 범위로 제한된다.

최종 답변이 확정된 후에는 Main 모델과 분리된 추출 요청으로 신규 Fact를 판정한다.
사용자의 최신 발화와 해당 요청에서 성공한 **비기억조회 도구 결과**만 신규 사실의
근거로 삼는다. 기억 조회 결과를 새 사실처럼 재저장하지 않는다.
완전한 조회 질문만 있는 경우 Fact가 없으면 원문 발화 저장을 건너뛸 수 있다.
추출 오류가 발생해도 원문 근거를 무조건 폐기하지 않는 정책은 유지한다.
저장 작업은 비동기로 진행되며, 같은 사용자의 다음 요청은 직전 작업을 기다린다.

현재 직접 발화와 도구 결과는 저장된 과거 사실보다 우선하며,
같은 시간 범위의 상충 Fact는 `created_at` 및 실제 유효 기간으로 판단한다.
조회 순위나 `last_seen_at`을 진위 판단으로 대체하지 않는다.

[Memory 저장소 위치 및 안전한 이전](memory-repository.md) ·
[전체 런타임 구조](architecture.md)
