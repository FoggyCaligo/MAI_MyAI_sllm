# MAI의 장기기억 연동과 실행 수명주기

MAI는 **장기기억 엔진을 직접 구현하지 않고**, 별도 저장소 [MCP_memorizer](https://github.com/FoggyCaligo/MCP_memorizer)의 설치된 Python 패키지와 검색 전용 MCP 도구를 사용한다. 이 문서는 MAI가 메모리 엔진을 **언제·어떤 사용자 권한과 근거로 호출하는지**를 설명한다.

엔진 자체의 그래프·사실 추출·Concept·SQLite 검색·랭킹 알고리즘은 [MCP_memorizer 엔진 문서](https://github.com/FoggyCaligo/MCP_memorizer/blob/dev/docs/architecture.md), [회수 계약](https://github.com/FoggyCaligo/MCP_memorizer/blob/dev/docs/recall.md), [추출 계약](https://github.com/FoggyCaligo/MCP_memorizer/blob/dev/docs/extraction.md)을 원본으로 삼는다. 문자 경로 분해 알고리즘의 원본은 같은 개발자가 운영하는 [Sentence_Breaker](https://github.com/FoggyCaligo/Sentence_Breaker/blob/dev/docs/algorithm.md)다.

## MAI가 소유하는 것

- **계정·권한:** 로그인 사용자 식별자와 데이터 소유자 `db_id`를 분리하고, 현재 계정의 `db_id`를 메모리 사용자 식별자로 전달한다. 모델이 임의로 다른 사용자 ID를 선택하지 못하게 한다.
- **최종 답변 결정:** Main 및 필요 시 Verifier의 작업이 끝나고 답변 전달이 확정된 이후에만 사실 추출·저장을 진행한다. Verifier 기본 활성화 여부는 배포 설정 `MAI_VERIFIER_ENABLED`가 정한다.
- **추출용 모델:** 같은 선택 모델을 별도 요청으로 사용한다. 사실 추출 프롬프트 및 JSON 계약 자체는 MCP_memorizer 패키지가 소유한다.
- **추출 입력 선택:** 최신 사용자 원문, 직전 도우미 메시지, 확정된 최종 답변, 성공한 비기억조회 도구 결과의 첫 `context_content`를 추출 호출에 제공한다. 실패한 실행과 기억 조회 결과를 신규 사실 근거로 재주입하지 않는다.
- **실행 수명주기:** 추출·저장을 비동기 후처리로 관리하고, 같은 사용자의 다음 요청 전에 앞선 후처리 완료를 기다린다. 서버 종료 시 취소될 수 있으며 서버 재시작 후 자동 이어받는 영속 큐는 아니다.
- **검증 근거:** 성공한 `memorizer__memory_search` 출력과 호출 인수를 실행 순서대로 Verifier에 제공한다. 검색 호출을 임의로 병합하거나 중복 제거한 새 근거로 위장하지 않는다.

추출 결과에 기억이 없을 때 검색 자체를 이유로 원문 발화를 새 사실로 승격시키지 않는다. 기억만 묻는 질문에서는 Facts가 비어 있을 수 있다. 이미 알고 있는 사실의 반복 조회와 새로운 사용자 직접 진술을 구분하는 정책은 [MCP_memorizer 추출 문서](https://github.com/FoggyCaligo/MCP_memorizer/blob/dev/docs/extraction.md)를 따른다.

## 모델에 제공되는 검색 도구

MAI는 `memorizer__memory_search`라는 **MCP 서버별 이름**으로 도구를 공개한다. 모델은 `tool_manual`에서 활성화하고, 활성화 후 제공되는 구조화 스키마로 호출한다. 이전 네이티브 `memory_search`를 별도로 등록하지 않는다.

엔진의 `query`·`limit`·`node_id` 입력과 실제 검색/정렬/확장 규칙은 [MCP_memorizer 검색 계약](https://github.com/FoggyCaligo/MCP_memorizer/blob/dev/docs/recall.md)을 참고한다. `query`는 단일 검색어 또는 AND(`&`), OR(`|`), NOT(`!`), 괄호(`()`)의 Boolean 검색식을 받는다. 키워드 각각은 기존 개념 기반 검색을 수행하고 조건에 따라 결과를 결합·제외한다. MAI는 전체 장기기억을 매 턴 무조건 프롬프트에 넣지 않는다.

현재 작업에서 `switch_workspace(target="client")`를 선택했더라도 **메모리·인터넷·브라우저·호스트 제어 도구는 MAI 서버인 host에서 실행**한다. 선택은 작업별 Workspace 호환 도구에만 적용된다.

## 정정과 시간 범위

현재 사용자의 직접 발화와 이번에 성공한 도구 결과는 과거 기억보다 우선한다. 같은 대상·속성·시간 범위에서 충돌하면 `created_at`과 사실의 유효 범위를 비교하도록 Main/Verifier에 지시한다. 조회 순위, 저장 횟수, `last_seen_at`만으로 현재 사실을 확정하지 않는다.

옛 사실을 자동 삭제하거나 의미상 모순을 해결하는 별도 엔진이 완성됐다고 주장하지 않는다. 기억 검색 결과가 비어도 실제 기억 부재를 단정하지 않는다.

## 데이터 보관·설치·검증

MAI 기본 개인 데이터 디렉터리는 `MAI_MEMORY/`다. 메모리와 분해기 누적 DB는 그 아래의 `memory.sqlite3`, `sentence_breaker.sqlite3`를 사용한다. **이 위치는 MAI 호스트의 설정**이며 독립 패키지의 기본 경로와 다르다.

- 개인 DB 저장소와 경로 이전: [MAI 데이터 저장소 관리](memory-repository.md)
- 기억 MCP 연결·활성화: [MCP_memorizer 연동](../mai/mcp_tools/README.md)
- 전체 요청 실행 흐름: [MAI 실행 구조](architecture.md)
- 모델·Portable 설치: [설치 안내](../setup.md), [Portable 안내](../portable/README.md)
- MAI 회귀 테스트: `tests/test_external_memorizer_integration.py`, `tests/test_unified_memory_lookup.py`, `tests/test_recall_ranked_limit.py`

메모리 엔진 내부의 그래프 스키마·색인·Sentence_Breaker 알고리즘 변경은 **MAI 본체 문서가 아닌 원본 프로젝트의 코드·문서에서 관리**한다.
