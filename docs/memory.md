# MAI 네이티브 장기기억

MAI는 **내장 Python 기억 엔진**을 사용한다. 외부 MCP_memorizer 서버를 실행하거나 별도 저장소의 버전을 맞출 필요가 없다. 메모리 도구는 서버(host)에서만 실행되는 네이티브 `memory_search` 하나이며, 모델에게 저장 도구를 제공하지 않는다.

## 검색

모델은 `tool_manual`에서 `memory_search`를 활성화한 후 다음 인자를 사용한다.

| 인자 | 설명 |
|---|---|
| `query` | 단일 키워드 또는 AND(`&`), OR(`|`), NOT(`!`), 괄호 검색식 |
| `limit` | 최대 검색 결과 수; 단독 사용 시 최근 목록(1~50) |
| `node_id` | 기존 검색 결과의 노드를 한 단계 확장; 다른 선택자와 병용 불가 |

```text
("병력" | "질환" | "질병" | "아픔" | "disease") & ("어릴 적" | "어린 시절" | "유년기" | "childhood")
```

키워드별 기존 Concept 정확 일치/SQLite FTS5 검색 결과를 기억 노드별 집합 연산으로 결합한다. 인용부호 안은 하나의 표현이다. 단일 검색어는 기존 어휘 검색 방식이다. 동의어/다국어를 자동 확장하거나 의미 유사도 임베딩 검색을 하지는 않는다. 사용자의 기억을 다른 사용자의 기억과 섞지 않는다.

## 자동 저장 — 모델에게 `memory_save`를 주지 않음

1. 사용자 요청에 대한 MAI의 **최종 답변이 확정**된다.
2. 호스트가 최신 사용자 원문, 직전 도우미 메시지, 최종 답변, 성공한 비기억조회 도구 결과를 근거로 별도 사실 추출을 실행한다.
3. 추출한 Fact와 원문 근거를 사용자별 SQLite 그래프에 기록한다. 단순 기억 조회만으로 신규 사실이 없으면 중복 저장을 제한한다.
4. 같은 사용자의 다음 요청은 앞선 저장 작업 완료를 기다린다. 추출 실패 시 원문 보존을 우선하고, 검색 결과를 새 사실 근거로 재주입하지 않는다.

저장은 독립적인 후처리이며 모델이 작업 도중 저장 도구를 호출할 수 없다. 모델 응답이 임시 상태일 때 잘못된 추론을 기억으로 저장하지 않는다. 다만 재시작 이후 정확히 한 번 처리하는 영속 작업 큐는 없다.

## 코드 및 데이터

- `mai/memory/tools.py`: 네이티브 `memory_search`와 도구 설명
- `mai/memory/policy.py`: 기억 검색·시간 우선순위 프롬프트
- `mai/memory/recall/query.py`, `service.py`: 검색식 파싱·어휘 검색·집합 연산
- `mai/memory/graph/`, `index/`: 사용자별 Fact·Utterance·Concept·SQLite 검색
- `mai/memory/extraction/service.py`: 최종 답변 후 사실 추출
- `mai/app/runtime.py`: 네이티브 등록과 백그라운드 저장 수명주기

기존 `MAI_MEMORY` 폴더의 `memory.sqlite3`와 `sentence_breaker.sqlite3` 데이터를 그대로 사용한다. DB를 재생성하거나 데이터를 이동하지 않는다. Sentence_Breaker는 별도 원본 패키지 의존성으로 유지한다.

기존 MCP 설정 파일의 `memorizer` 항목은 사용하지 않고 기본 설정 초기화 시 제거한다. 나머지 기능별 MCP 도구는 그대로 유지한다. 외부 프로젝트 [MCP_memorizer](https://github.com/FoggyCaligo/MCP_memorizer)는 독립적으로 존재하나 MAI의 런타임 의존성은 아니다.
