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

로그인 세션은 현재 Python 서버 메모리에 있으므로, 단순 앱 전환이나 페이지 재진입에는 유지되지만 Python 서버 자체를 재시작하면 다시 로그인해야 한다. 채팅 기록과 graph DB는 SQLite에 남는다.

파일 도구는 owner에게만 열려 있다. owner에 대해서는 애플리케이션 수준의 workspace confinement를 두지 않으며, 절대 경로와 부모 경로를 그대로 사용할 수 있다. 실제 OS/filesystem 권한이 최종 경계다. `file_tree`, `file_search`, `file_text_search`, `file_read`는 큰 결과를 한 번에 문맥에 밀어 넣지 않도록 pagination을 제공하며, 다음 cursor/line을 통해 계속 읽을 수 있다.

`file_text_search`는 의미 검색을 하지 않고 모델이 지정한 문자열을 literal substring으로만 찾는다. UTF-8 등 지정 인코딩으로 읽지 못한 파일은 성공으로 숨기지 않고 `decode_failures`에 명시한다.

`file_create`는 기존 파일을 덮어쓰지 않는다. `file_update`는 같은 디렉터리의 임시 파일에 완전히 기록한 뒤 원자 교체하여 중간 실패로 원본이 반쯤 잘리는 상황을 피한다. `file_delete`는 디렉터리를 대신 삭제하지 않는다. 각각의 파일 존재/권한/I/O 오류는 그대로 실패로 드러난다.

`file_download_link`가 발급하는 `/download/<token>` URL은 기본 1시간 동안만 유효하며, 해당 token을 발급한 owner 로그인 세션도 동시에 필요하다. token 없음은 404, 만료는 410, 다른 사용자 접근은 403으로 구분한다. 서버가 재시작되면 메모리의 임시 download token은 사라진다.

아직 이 단계에서는 PDF/DOCX, 이미지 분석, 터미널, 코드 검색은 연결하지 않았다. 후속 PR에서 각각 별도 work tool로 추가한다.
