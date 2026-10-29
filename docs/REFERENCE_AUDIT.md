# MAI 실행 경로·미사용 참조 점검 (3·4단계)

## 3단계 — SQLite 저장 경로

- `MEMORY_DB_PATH`, `SENTENCE_BREAKER_DB_PATH`, `CHAT_DB_PATH`, `LOG_DB_PATH`의 기본값을 `mai/app/server.py`의 `_DB_PATH_DEFAULTS`로 한곳에서 정의한다.
- `resumable_chat.py`의 감사 로그도 동일한 설정 접근 경로를 사용한다.
- 상대경로는 기존과 같이 **프로세스 작업 디렉터리(CWD)** 기준이며, `.env` 설정이 우선한다. 기존 DB를 자동 이동하거나 초기화하지 않는다.
- `LOG_DB_PATH`를 `.env.example`에 다시 명시한다. 해당 설정의 예시 누락은 실제 로그 저장 기능을 없앤 것이 아니다.
- 사용자 방침에 따라 `memory.sqlite3`, `chat.sqlite3`, `sentence_breaker.sqlite3`의 Git 추적 정책을 변경하지 않는다. `log.sqlite3*`는 종전대로 무시한다.
- `mai/app/chat_sessions.py`의 과거 스키마/ID 마이그레이션은 기존 설치 데이터 보존에 필요하므로 삭제하지 않는다.

## 4단계 — 참조 감사

읽기 전용 정적 분석 명령:

```bash
python scripts/audit_references.py
```

이 도구는 `mai/**/*.py` 전체를 대상으로 하며, 루트·`scripts/`·`tests/`의 Python 파일에서 정적 `import`/`from ... import ...` 관계를 수집한다. 구문/파일 읽기에 실패하면 명확하게 실패한다. 출력된 항목은 **삭제 확정이 아니라 검토 후보**다.

정적 import만으로는 다음 사용을 증명하거나 부정할 수 없다.

- `run_server.py` 같은 직접 실행 진입점
- FastAPI 데코레이터 및 등록을 통한 간접 호출
- `importlib` 및 런타임 플러그인 탐색
- 템플릿/HTML/JS 등의 정적 리소스, 파일명에 의한 런타임 접근
- Windows PowerShell 설치 스크립트 및 문서에서만 호출되는 명령

### 실제로 확인한 주요 경로

| 구성 요소 | 확인된 사용처 | 판정 |
| --- | --- | --- |
| `run_server.py` | 공식 서버 실행 경로, `resumable_chat.install()` 호출 | 유지 |
| `mai/app/server.py` | FastAPI 앱 생성, API·DB·런타임 설정 | 유지 |
| `mai/app/resumable_chat.py` | 작업 기반 채팅, `/` UI, `/chat/jobs` 라우트 | 유지 |
| `mai/app/chat_sessions.py` | 세션 저장, 기존 DB 형식 마이그레이션 | 유지 |
| `mai/app/audit_log.py` | 실행 및 도구 감사 기록 | 유지 |
| `mai/memory/recall/service.py` | 기억 검색 및 노드 확장 | 유지 |
| `mai/memory/index/sqlite_fts.py` | SQLite FTS5 개념 색인 | 유지 |
| `mai/agent/tool_results.py` | 대형 도구 출력의 저장·페이지화 | 유지 |
| `scripts/install_windows.ps1` | README 및 setup.md의 설치 명령 | 유지 |

### 현재 삭제/병합 보류

현재 연결 상태를 확인한 위 구성 요소에서 **안전한 미사용 파일 삭제를 확정하지 못했다**. 참조가 없다는 정적 분석 결과만으로 삭제하지 않는다. 전체 코드·테스트·브라우저 UI 실행을 통한 동적 검증 없이 미사용 코드를 단정하지 않는다.

검증이 가능한 작업 환경에서 후속 확인:

```bash
python scripts/audit_references.py
python -m pytest
```

웹 UI 로그인, 새 채팅, 대화 복원 및 이어받기, 감사 로그, 기존 SQLite 데이터 유지 여부를 회귀 검사해야 한다.

## 종합 정적 감사

추가된 읽기 전용 분석:

```bash
python scripts/audit_codebase.py
python scripts/audit_codebase.py --json
python -m pytest tests/test_audit_codebase.py
```

- Python AST로 모듈 정적 import 참조, 미참조 모듈 후보, docstring-only 파일을 검토한다.
- 모듈 import 순환(SCC), 대형/분기 집중 함수, 완전히 동일한 함수 AST를 후보로 보고한다.
- 테스트 모듈에서 직접 import되지 않은 모듈을 별도로 표시한다. **테스트 커버리지 측정이 아니다.**
- JSON 출력은 자동화용으로 제공하며, 기존 코드나 파일을 수정하지 않는다.
- 동적 import, UI, 등록 기반 실행, 파일 경로 참조 및 외부 소비는 정적 분석만으로 확정할 수 없다.
- 실제 커버리지, 실행 시간, 유사 코드(의미상 중복), 외부 라이브러리 감사는 별도 도구와 실행 환경이 필요하다.
- 구문 분석이나 파일 읽기 실패는 오류로 드러나고 정상 결과로 위장하지 않는다.
