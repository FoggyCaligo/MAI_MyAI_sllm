# CodeAct 원격 실행 및 Windows/Linux 워커

## 지원 범위

MAI 호스트는 인증된 Owner 계정의 활성 원격 세션 하나를 선택한다. 모델은
`tool_manual`로 필요한 도구를 활성화한 후 CodeAct에서 각 도구 호출의
`is_remote` 값을 명시적으로 선택한다. 서버는 사용자 문장에 대한
문자열 규칙으로 원격 대상을 결정하지 않는다.

```python
local = await tool("file_read", {"path": "local.txt"}, is_remote=False)
remote = await tool("file_read", {"path": "remote.txt"}, is_remote=True)
print(local["ok"], remote["ok"])
```

원격 실행 대상은 사용자가 채팅 중인 웹 브라우저가 아니라 연결된 워커가
실행되는 컴퓨터다. 원격 경로는 해당 컴퓨터의 파일 시스템이고 `terminal_run`
명령은 해당 컴퓨터의 OS를 따른다. Linux 기본 셸은 `/bin/sh`이며
`shell="powershell"`은 지원하지 않고 실패한다. Windows 기본 셸은 CMD다.

원격 워커에서 실제로 실행되는 기능은 파일 읽기·쓰기·검색·삭제·이동·복사,
코드 검색·편집, CSV, 구조화 문서 편집, 터미널, 계산기, 현재 시각,
공개 웹 검색·조회, 시세 조회, 브라우저의 열기·스냅샷·클릭·입력·스크롤·키 입력·닫기다.
브라우저 세션은 한 인증된 원격 연결의 Python 프로세스 안에 보관되고 연결 종료
시 정리한다. `image_analyze`는 MAI 호스트에서 해당 도구가 등록되고 원격 워커에도
`MAI_REMOTE_VISION_MODEL`이 설정된 경우에만 원격에서 수행할 수 있다.
원격 Ollama 주소는 `MAI_REMOTE_OLLAMA_HOST`로 설정 가능하며 기본은
`http://127.0.0.1:11434`다. 원격 모델이 없으면 오류가 발생하며 호스트의
모델로 대체하지 않는다.

## 호스트에 귀속된 데이터

`memory_search`는 서버 소유 DB의 사용자별 메모리와 요청별 Working Graph를,
`tool_result_read`는 현재 요청의 서버측 원문 결과 저장소를 사용한다.
`file_download_link` 역시 서버가 관리하는 다운로드 토큰을 만든다.
이 데이터를 원격 PC에 복제해 별도의 메모리·원문·다운로드 상태를 만들지 않는다.
이 도구들을 `is_remote=True`로 호출하면 원격 컴퓨터에서 실행되었다고
거짓으로 응답하지 않으며, CodeAct는 미지원 실행 대상을 `ok=false` 결과로 반환한다.
호스트 데이터를 다룰 때는 `is_remote=False`를 사용한다.

## 설치 및 접속

MAI 호스트에서 Owner 권한으로 원격 세션을 생성한 뒤 서버가 제공한
`session_id`와 `agent_secret`을 원격 연결에 사용한다.
공개 HTTPS 엔드포인트가 필요하다. 서버와 컴퓨터의 공개 접속 여부는 별개의 설정이다.

Windows에서는 MAI 서버가 제공하는 `remote-agent.ps1`을 사용한다.
스크립트는 SHA-256으로 검증된 워커 번들을 설치하고 Python 클라이언트를 실행한다.

Linux에서는 동일한 세션 정보를 아래처럼 POSIX 셸 스크립트에 전달한다.
인증값은 비밀이므로 명령줄 이력과 로그에 남기지 않도록 주의한다.

```sh
sh remote-agent-linux.sh --server https://MAI_HOST --session SESSION_ID --key AGENT_SECRET
```

Linux에는 Python 3.11 이상, python3-venv, curl, unzip, sha256sum과 Chromium의
필수 시스템 라이브러리가 필요하다. 실행기는 원격 전용 패키지만 설치한다.
Playwright Chromium 다운로드 또는 시스템 라이브러리 설치에 실패하면
명시적 오류로 남으며 브라우저 도구는 준비 완료라고 가정하지 않는다.
`remote-requirements.txt`를 사용하므로 호스트의 음성 인식·메모리 모델
의존성을 전부 설치하지 않는다.

둘 다 네이티브 Python 클라이언트를 한 연결 동안 상주시켜
`native.invoke` 요청과 응답을 처리한다. 요청에 대응하는 워커 도구가 없거나
권한·검증·네트워크 오류가 발생하면 `ok=false` 및 실제 오류 정보가 모델에
전달된다. 실패를 로컬 도구 성공으로 대체하지 않는다.

## 아직 검증되지 않은 항목

- Windows 및 Linux 실기기 통합 실행과 전체 pytest는 이 작업 환경에서 수행하지 못함
- 원격 배포 대상별 Playwright/Chromium 설치 의존성 및 실제 브라우저 세션 확인 필요
- CodeAct의 제한형 in-process Python은 OS 수준 보안 샌드박스가 아님.
  Trial에 대한 CodeAct 활성화는 별도 격리 구현 전까지 금지해야 함
- 원격 워커의 임시 파일 정리와 최종 답변 전달 완료 이벤트를 통합한 수명주기는
  별도로 검증·완성해야 함
