# Sentence_Breaker — 원본 알고리즘 및 연동 문서 안내

Sentence_Breaker는 **MAI 및 MCP_memorizer와 같은 개발자가 만든 독립 Python 패키지**다. 원본 저장소는 [FoggyCaligo/Sentence_Breaker](https://github.com/FoggyCaligo/Sentence_Breaker)이고 `pyproject.toml`의 작성자는 `bigla`다.

문자별 연결 경로, 문장 층, SQLite 스키마, 연속 경로 생존 상태, 동적 계획법 비교 규칙, **분해 후 학습** 순서를 포함한 알고리즘 명세는 [Sentence_Breaker 원본 문서](https://github.com/FoggyCaligo/Sentence_Breaker/blob/dev/docs/algorithm.md)로 이관했다. 이 문서는 별도의 알고리즘 사본을 보유하지 않으며 단일 원본 링크만 유지한다.

현재 복구 브랜치는 `mai/memory/segmenter.py`의 Native `SentenceBreakerSegmenter`로 Sentence_Breaker를 직접 호출하고, 그 결과를 `mai/memory/runtime.py`에서 Concept 그래프·FTS5 색인에 연결한다.

## MAI가 설정하는 경계

MAI는 Sentence_Breaker 내부 알고리즘을 복제·수정하지 않는다. Native 기억 엔진에서 분해기 어댑터를 직접 호출하고 누적 분해 DB 경로를 제공한다. 기본 경로는 `MAI_MEMORY/sentence_breaker.sqlite3`다.

누적 DB가 다르면 같은 문자열에 대해 다른 분해 결과가 나올 수 있으므로, 기존 기억 데이터를 이전할 때에는 **`memory.sqlite3`와 `sentence_breaker.sqlite3`를 함께 보존**해야 한다. 데이터 백업 및 이전 방법은 [MAI 개인 DB 저장소 문서](memory-repository.md)를 따른다.

원본 저장소의 `LANG_GRAPH_LAYER_DESIGN.md`에는 설계 아이디어도 포함되어 있다. 그 문서에 등장하는 선택적 기능이 현재 구현됐다는 뜻은 아니다.
