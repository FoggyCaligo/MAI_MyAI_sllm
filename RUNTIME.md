# Mai Runtime

## 1. 환경 설정

```powershell
Copy-Item .env.example .env
```

기본 모델은 아래와 같다.

```env
MAI_OLLAMA_MODEL=gemma4:e4b
MAI_OLLAMA_IMAGE_MODEL=gemma4:12b
MAI_OWNER_ID=<접속에 사용할 owner ID>
```

대화 모델은 `.env`에서만 관리하고 UI에서는 읽기 전용으로 표시한다. 이미지 모델도 별도 `.env` 값으로 관리한다.

시장 provider도 `.env`에서 scope별로 관리한다.

```env
MAI_MARKET_KR_EQUITY_PROVIDER=yahoo
MAI_MARKET_GLOBAL_EQUITY_PROVIDER=yahoo
MAI_MARKET_INDEX_PROVIDER=yahoo
MAI_MARKET_FX_PROVIDER=yahoo
```

## 2. 설치

```powershell
python -m pip install -r requirements.txt
```

## 3. 로컬 실행

```powershell
python run_server.py
```

기본 주소:

```text
http://127.0.0.1:8000
```

서버는 localhost에만 bind한다. 외부 공개를 위해 bind 주소를 넓히지 않는다.

## 4. Tailscale Funnel 공개

Tailscale이 설치되고 로그인된 Windows 환경에서:

```powershell
.\start_public_tailscale.ps1
```

또는:

```cmd
start_public_tailscale.cmd
```

스크립트는 로컬 Mai 서버를 `127.0.0.1:8000`에서 실행한 뒤 Tailscale Funnel reverse proxy를 설정한다.

```text
tailscale funnel --bg --yes http://127.0.0.1:8000
```

Funnel 설정 실패, Tailscale 미설치, Python 서버 조기 종료는 성공으로 숨기지 않고 오류로 종료한다.

## 5. 현재 UI/runtime 범위

- MK4 스타일의 Mai 채팅 UI
- `.env` 기반 대화 모델 표시 및 사용
- 허용 ID 로그인 / owner role 구분
- 새로고침 및 브라우저 재진입 후 SQLite chat history 복원
- 파일 업로드 (`.mai_uploads/`)
- 현재 `AgentLifecycle` 호출
- work tool log 표시
- owner용 파일 조회 도구:
  - `file_tree`: 디렉터리 구조 조회
  - `file_search`: 파일명/경로 glob 검색
  - `file_text_search`: 파일 본문 literal substring 검색
  - `file_read`: 텍스트 파일 줄 단위 읽기
- owner용 파일 변경/전달 도구:
  - `file_create`: 새 텍스트 파일 생성
  - `file_update`: 기존 텍스트 파일 전체 내용을 원자적으로 교체
  - `file_delete`: 명시한 파일 하나 삭제
  - `file_download_link`: 현재 브라우저에서 받을 수 있는 1시간 임시 링크 생성
- owner용 문서/이미지 도구:
  - `document_read`: PDF 페이지 또는 DOCX 문단 단위 읽기
  - `image_analyze`: 독립 이미지 모델로 이미지 분석
- owner용 시스템 도구:
  - `terminal_command`: 호스트 shell 명령 실행
- owner용 코드 도구:
  - `code_index`: Python source를 AST로 읽어 compact in-memory repository map 생성
  - `code_search`: 현재 structural index에서 관련 file/symbol 검색
- web/current-information 도구:
  - `latest_search`: 모델이 작성한 query로 recent/news 검색
  - `web_research`: 모델이 작성한 objective + queries로 검색하고 public page evidence 구성
  - `market_snapshot`: 구조화된 provider scope로 asset lookup/snapshot

로그인 세션은 현재 Python 서버 메모리에 있으므로, 단순 앱 전환이나 페이지 재진입에는 유지되지만 Python 서버 자체를 재시작하면 다시 로그인해야 한다. 채팅 기록과 graph DB는 SQLite에 남는다.

파일 도구는 owner에게만 열려 있다. owner에 대해서는 애플리케이션 수준의 workspace confinement를 두지 않으며, 절대 경로와 부모 경로를 그대로 사용할 수 있다. 실제 OS/filesystem 권한이 최종 경계다. `file_tree`, `file_search`, `file_text_search`, `file_read`는 큰 결과를 한 번에 문맥에 밀어 넣지 않도록 pagination을 제공하며, 다음 cursor/line을 통해 계속 읽을 수 있다.

`file_text_search`는 의미 검색을 하지 않고 모델이 지정한 문자열을 literal substring으로만 찾는다. UTF-8 등 지정 인코딩으로 읽지 못한 파일은 성공으로 숨기지 않고 `decode_failures`에 명시한다.

`file_create`는 기존 파일을 덮어쓰지 않는다. `file_update`는 같은 디렉터리의 임시 파일에 완전히 기록한 뒤 원자 교체하여 중간 실패로 원본이 반쯤 잘리는 상황을 피한다. `file_delete`는 디렉터리를 대신 삭제하지 않는다. 각각의 파일 존재/권한/I/O 오류는 그대로 실패로 드러난다.

`file_download_link`가 발급하는 `/download/<token>` URL은 기본 1시간 동안만 유효하며, 해당 token을 발급한 owner 로그인 세션도 동시에 필요하다. token 없음은 404, 만료는 410, 다른 사용자 접근은 403으로 구분한다. 서버가 재시작되면 메모리의 임시 download token은 사라진다.

`document_read`는 `.pdf`와 `.docx`만 명시적으로 지원한다. PDF는 page, DOCX는 paragraph 단위로 pagination하고, 다른 문서 형식을 임의로 텍스트 파일처럼 fallback하지 않는다. 파싱 실패도 그대로 오류로 드러난다.

`image_analyze`는 일반 대화 모델과 분리된 `MAI_OLLAMA_IMAGE_MODEL`을 사용한다. 기본값은 `gemma4:12b`이며, 이미지 bytes를 Ollama `/api/chat`의 message `images` 입력으로 전달한다. framework는 분석 prompt의 의미를 재해석하거나 바꾸지 않는다.

`terminal_command`는 owner가 작성한 `command` 문자열을 의미적으로 검사하거나 재작성하지 않고 host shell에 그대로 전달한다. `cwd`, 선택적 `timeout_seconds`, 출력 `encoding`을 구조적으로 지정할 수 있다. 명령이 non-zero exit로 끝나면 `ok=false`, `returncode`, `stdout`, `stderr`를 그대로 반환하여 실패를 성공처럼 숨기지 않는다. 잘못된 cwd, timeout, shell/OS 실행 오류는 실제 예외로 드러난다. OS, shell, filesystem, registry, process, 계정 권한이 최종 실행 경계다.

`code_index`는 옛 MK4의 compact repository map 방식처럼 요청 root 아래 Python 파일을 직접 읽고 AST를 파싱한다. index에는 imports, classes/methods, function signatures, routes, registered tool names, config constants, tests 같은 구조 정보가 들어간다. index state는 현재 Python 프로세스 메모리에만 존재하며 별도 파일이나 DB로 저장하지 않는다. parse 실패는 `parse_errors`로 경로와 오류를 그대로 반환한다.

`code_search`는 현재 in-memory structural index의 path/symbol/구조 텍스트를 검색해 관련 file과 symbol을 좁힌다. index가 아직 없거나 요청 root가 기존 indexed root와 다르면 현재 source로 자동 rebuild한다. 같은 root에서 source가 바뀐 경우에는 기존 index를 조용히 덮어쓰지 않으므로, 최신 구조가 필요하면 모델이 `code_index`를 다시 호출한다. 이후 상세 구현 확인은 `file_read`로 실제 source를 읽는다.

`latest_search`는 모델이 작성한 `query`를 그대로 recent/news provider에 전달한다. Framework는 query를 의미적으로 분류하거나 재작성하지 않는다.

`web_research`는 모델이 `objective`뿐 아니라 `queries` 배열도 직접 작성한다. Framework는 objective에서 검색어를 자동 생성하지 않고 전달된 queries만 실행한다. 상위 public HTTP(S) page를 읽을 때 redirect 목적지도 매 단계 public address인지 다시 확인한다. 페이지별 실패는 `page_errors`에 명시된다.

`market_snapshot`은 query 문자열이나 symbol을 보고 asset 종류를 추론하지 않는다. 모델이 `provider_scope` (`kr_equity`, `global_equity`, `index`, `fx`)와 `operation` (`lookup`, `snapshot`)을 명시한다. `lookup` 결과의 실제 `provider_symbol`을 이후 `snapshot`에 사용할 수 있다. Scope별 provider는 `.env` 설정으로 정하며, provider가 실패하거나 등록되지 않았을 때 다른 provider로 자동 fallback하지 않는다.

세 web/market tool의 상세 계약은 `WEB_MARKET_CONTRACT.md`를 참조한다.

이제 기존 MK4에서 우선 복원 대상으로 정한 주요 model-visible capability는 모두 runtime에 연결되어 있다. 다음 단계는 실제 로컬 실행/통합 검증과 사용성 개선이다.
