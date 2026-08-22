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

## 5. Single-agent lifecycle

현재 runtime은 MK4와 비슷한 **하나의 agent loop**를 사용한다. 별도의 mandatory memory-discovery model phase와 post-answer memory-completion model loop를 실행하지 않는다.

```text
User
 ↓
Framework turn initialization
 ↓
Agent model
 ├─ answer directly
 ├─ node_lookup
 ├─ recall_memory
 └─ work tool
      ↓
      result → Agent model
 ↓
Final answer + memory_mutations[]
 ↓
Framework fixes answer text
 ↓
Framework executes memory mutations
 ↓
Answer release
```

따라서 tool이나 memory recall이 필요 없는 일반 대화는 모델이 첫 round에 final answer를 반환할 수 있고, **대화 모델 호출 1회**로 끝날 수 있다.

Memory가 필요한 경우에는 같은 loop 안에서 모델이 `node_lookup`/`recall_memory`를 선택한다. Framework가 사용자 문장의 의미를 보고 memory route를 강제하지 않는다.

Final answer는 `content`와 최소 1개의 `memory_mutations`를 동시에 구조화해서 반환한다. Framework는 `content`를 먼저 immutable fixed answer로 확정하고, 그 뒤 기존 `WriteMemoryTool`/`ReviseMemoryTool` 계약으로 mutation plan을 실행한다. Mutation 실패 시 fixed answer를 성공 응답으로 release하지 않는다.

별도의 memory `done` model round는 없다. 계획된 mutation 실행이 모두 성공하면 Framework가 memory status를 `done`으로 확정한다.

Agent loop 자체에는 임의의 global round cap을 두지 않는다.

## 6. 현재 UI/runtime 범위

- MK4 스타일의 Mai 채팅 UI
- `.env` 기반 대화 모델 표시 및 사용
- 허용 ID 로그인 / owner role 구분
- 새로고침 및 브라우저 재진입 후 SQLite chat history 복원
- 파일 업로드 (`.mai_uploads/`)
- single-loop `AgentLifecycle` 호출
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

## 7. File path provenance

파일 도구는 owner에게만 열려 있다. owner에 대해서는 애플리케이션 수준의 workspace confinement를 두지 않으며, 절대 경로와 부모 경로를 탐색 root로 사용할 수 있다. 다만 기존 파일을 대상으로 하는 action은 모델이 임의 경로를 생성해서 바로 실행할 수 없고, 현재 turn에서 Framework가 실제로 확인한 concrete path만 사용할 수 있다.

current-turn path provenance는 다음 경로에서 생성된다.

- 현재 로그인 사용자의 실제 upload attachment
- `file_tree`가 반환한 file entry
- `file_search`가 반환한 file match
- `file_text_search`가 반환한 matched file
- `code_index`의 key file
- `code_search`의 result file
- 성공한 `file_create`의 새 path

provenance가 없는 동안 `file_read`, `file_update`, `file_delete`, `file_download_link`, `document_read`, `image_analyze`는 model schema에 노출되지 않는다. provenance가 생기면 다음 model round부터 실제 확인된 path만 `enum`으로 노출된다. `document_read`는 그중 PDF/DOCX만, `image_analyze`는 이미지 확장자만 노출한다. 실행 직전에도 provenance scope를 다시 검사하므로 모델이 schema 밖 경로를 반환하면 계약 실패로 드러난다.

`file_create`는 새 path를 만드는 역할이므로 provenance를 요구하지 않는다. 성공한 create path는 즉시 provenance에 등록되어 같은 turn의 후속 `file_read`/`file_update`/`file_delete`/`file_download_link`에서 사용할 수 있다. `file_delete` 성공 후 삭제된 path는 provenance에서 제거되어 다음 round의 기존-file action schema에서도 사라진다. 이 계약은 파일명 의미 추론이나 자연어 휴리스틱이 아니라 실제 tool/upload 결과의 normalized path identity로만 동작한다.

`file_tree`, `file_search`, `file_text_search`, `file_read`는 큰 결과를 한 번에 문맥에 밀어 넣지 않도록 pagination을 제공하며, 다음 cursor/line을 통해 계속 읽을 수 있다.

`file_text_search`는 의미 검색을 하지 않고 모델이 지정한 문자열을 literal substring으로만 찾는다. UTF-8 등 지정 인코딩으로 읽지 못한 파일은 성공으로 숨기지 않고 `decode_failures`에 명시한다.

`file_create`는 기존 파일을 덮어쓰지 않는다. `file_update`는 같은 디렉터리의 임시 파일에 완전히 기록한 뒤 원자 교체하여 중간 실패로 원본이 반쯤 잘리는 상황을 피한다. `file_delete`는 디렉터리를 대신 삭제하지 않는다. 각각의 파일 존재/권한/I/O 오류는 그대로 실패로 드러난다.

`file_download_link`가 발급하는 `/download/<token>` URL은 기본 1시간 동안만 유효하며, 해당 token을 발급한 owner 로그인 세션도 동시에 필요하다. token 없음은 404, 만료는 410, 다른 사용자 접근은 403으로 구분한다. 서버가 재시작되면 메모리의 임시 download token은 사라진다.

## 8. Document / image

`document_read`는 `.pdf`와 `.docx`만 명시적으로 지원한다. PDF는 page, DOCX는 paragraph 단위로 pagination하고, 다른 문서 형식을 임의로 텍스트 파일처럼 fallback하지 않는다. 파싱 실패도 그대로 오류로 드러난다.

`image_analyze`는 일반 대화 모델과 분리된 `MAI_OLLAMA_IMAGE_MODEL`을 사용한다. 기본값은 `gemma4:12b`이며, 이미지 bytes를 Ollama `/api/chat`의 message `images` 입력으로 전달한다. framework는 분석 prompt의 의미를 재해석하거나 바꾸지 않는다.

## 9. Terminal

`terminal_command`는 owner가 작성한 `command` 문자열을 의미적으로 검사하거나 재작성하지 않고 host shell에 그대로 전달한다. `cwd`, 선택적 `timeout_seconds`, 출력 `encoding`을 구조적으로 지정할 수 있다. 명령이 non-zero exit로 끝나면 `ok=false`, `returncode`, `stdout`, `stderr`를 그대로 반환하여 실패를 성공처럼 숨기지 않는다. 잘못된 cwd, timeout, shell/OS 실행 오류는 실제 예외로 드러난다. OS, shell, filesystem, registry, process, 계정 권한이 최종 실행 경계다.

## 10. Code tools

`code_index`는 옛 MK4의 compact repository map 방식처럼 요청 root 아래 Python 파일을 직접 읽고 AST를 파싱한다. index에는 imports, classes/methods, function signatures, routes, registered tool names, config constants, tests 같은 구조 정보가 들어간다. index state는 현재 Python 프로세스 메모리에만 존재하며 별도 파일이나 DB로 저장하지 않는다. parse 실패는 `parse_errors`로 경로와 오류를 그대로 반환한다.

`code_search`는 현재 in-memory structural index의 path/symbol/구조 텍스트를 검색해 관련 file과 symbol을 좁힌다. index가 아직 없거나 요청 root가 기존 indexed root와 다르면 현재 source로 자동 rebuild한다. 같은 root에서 source가 바뀐 경우에는 기존 index를 조용히 덮어쓰지 않으므로, 최신 구조가 필요하면 모델이 `code_index`를 다시 호출한다. 이후 상세 구현 확인은 `file_read`로 실제 source를 읽는다.

## 11. Web / market

`latest_search`는 모델이 작성한 `query`를 그대로 recent/news provider에 전달한다. Framework는 query를 의미적으로 분류하거나 재작성하지 않는다.

`web_research`는 모델이 `objective`뿐 아니라 `queries` 배열도 직접 작성한다. Framework는 objective에서 검색어를 자동 생성하지 않고 전달된 queries만 실행한다. 상위 public HTTP(S) page를 읽을 때 redirect 목적지도 매 단계 public address인지 다시 확인한다. 페이지별 실패는 `page_errors`에 명시된다.

`market_snapshot`은 query 문자열이나 symbol을 보고 asset 종류를 추론하지 않는다. 모델이 `provider_scope` (`kr_equity`, `global_equity`, `index`, `fx`)와 `operation` (`lookup`, `snapshot`)을 명시한다. `lookup` 결과의 실제 `provider_symbol`을 이후 `snapshot`에 사용할 수 있다. Scope별 provider는 `.env` 설정으로 정하며, provider가 실패하거나 등록되지 않았을 때 다른 provider로 자동 fallback하지 않는다.

세 web/market tool의 상세 계약은 `WEB_MARKET_CONTRACT.md`를 참조한다.

## 12. Inspection progress contract

Inspection/search/read 계열 work tool은 structural `progress_keys(result)`를 제공해야 한다.

Framework는 tool별로 current turn에서 이미 본 progress key를 누적한다.

```text
inspection 실행
→ 새 structural key 존재: tool 계속 사용 가능
→ 새 structural key 없음: 다음 model round부터 해당 tool schema 제거
```

이는 query 문자열 비교나 자연어 의미 판단이 아니며 임의 round cap도 아니다.

현재 주요 progress identity:
- `file_tree`: entry kind + path
- `file_search`: match kind + path
- `file_text_search`: path + line
- `file_read`: path + 실제 읽은 line range
- `code_index`: resolved key-file path
- `code_search`: resolved result-file path
- `document_read`: path + page/paragraph position
- `image_analyze`: image path
- `latest_search` / `web_research`: URL
- `market_snapshot`: provider symbol/time

Action tool (`file_create`, `file_update`, `file_delete`, `file_download_link`, `terminal_command`)은 정상적인 반복 action이 의미 있을 수 있으므로 inspection progress identity로 반복을 차단하지 않는다.

자세한 구조는 `WORK_TOOL_CONTRACT.md`를 참조한다.

## 13. 실패 처리

Framework는 tool/model/DB/hosting 실패를 성공으로 바꾸지 않는다. 임의 fallback, guessed path/ID, malformed tool auto-repair를 사용하지 않는다.

현재 구조의 다음 검증 단계는 로컬 전체 테스트와 실제 Ollama 실행에서 **plain chat 1 model round**, 필요한 tool/memory만 추가 round로 실행되는지 확인하는 것이다.
