# Windows·Linux 원격 Native 워커

이 문서는 현재 `dev`의 원격 PC 작업 경로를 설명한다. 별도 CodeAct 평가기나 `execute_code` 도구를 사용하는 방식이 아니다. 웹 UI를 인터넷에 공개하는 기능과 원격 PC에서 도구를 실행하는 기능은 별도다.

## 실행 계약

1. 모델의 최초 차례에는 `tool_manual`만 노출된다. 활성화한 도구의 실제 Pydantic 인수 계약이 다음 차례부터 제공된다.
2. Owner의 원격 지원 native 도구에는 `is_remote: bool = false`가 추가된다. 모델은 사용자 요청의 실행 대상을 기준으로 이를 선택하며, 경로·명령 문자열 패턴으로 원격 여부를 추측하지 않는다.
3. 생략 또는 `false`는 MAI 호스트에서 실행한다. `true`는 해당 Owner가 인증·연결한 원격 워커의 `native.invoke` 경로에서 실행한다.
4. 대상 워커가 없거나 연결이 끊겼거나 미지원 도구인 경우 명시적으로 실패하며, 로컬 실행으로 되돌리지 않는다. 원격 실패 유형과 메시지도 보존한다.
5. 결과는 전송용 envelope가 아니라 원래 도구의 결과를 모델에 보여준다.

예를 들어 `file_read`를 활성화한 뒤에는 다음 인수를 사용할 수 있다.

```json
{"path":"/home/owner/notes.txt","is_remote":true}
```

## 구현 및 도구 지원 범위

서버 측 등록과 라우팅은 `mai/app/runtime.py`, `mai/remote/routing.py`, `mai/remote/target.py`에 있다. 워커는 `mai/remote/python_worker.py`에서 공통 Native 도구 등록을 재사용한다.

원격 지원 범위는 파일·코드 작업, CSV 집계, 구조화 문서 편집, 터미널, 계산, 시각 조회, 웹 검색·조회, 한국 주식 시장 조회 및 브라우저 작업이다. 워커의 브라우저 세션 관리자는 동일 원격 연결에서 재사용된다.

`memory_search`, `tool_result_read`, `file_download_link`는 MAI 서버의 저장소·권한·토큰 상태에 종속되어 원격화되지 않는다. Ollama 전용 원격 `image_analyze` 도구는 폐기됐다. 메인 모델 이미지 입력은 MAI 서버에 전달된 첨부에 대한 llama.cpp 직접 입력이다.

`terminal_run`은 `shell`을 생략하면 Bash를 사용한다. Windows는 Git Bash, Linux는 Bash다. Windows 전용 명령은 `shell=cmd` 또는 `shell=powershell`을 명시한다. Windows 원격 노트북에는 Git for Windows가 설치되어 있어야 하며 설치 경로를 못 찾으면 오류를 반환한다. 지원하지 않는 셸 조합은 문법을 암묵적으로 변경하지 않고 오류로 반환한다. 체험 계정은 기존 읽기·파일 범위 제한을 유지한다.

## 연결과 설치

Owner의 원격 연결 UI에서 인증된 세션을 생성하고 대상 운영체제의 시작 명령을 선택한다. Windows는 `mai/app/static/remote-agent.ps1`, Linux는 `mai/app/static/remote-agent-linux.sh`의 흐름을 사용한다. 대상 컴퓨터는 HTTPS로 MAI에 접속해 작업을 폴링하고 결과를 반환한다.

Linux에는 Python 3.11 이상, `curl`, `unzip`, `sha256sum`, 작동하는 `python3 -m venv` 및 필요한 Chromium 시스템 라이브러리가 있어야 한다. 두 워커는 서버가 제공하는 인증된 소스 아카이브를 받아 SHA-256을 확인한다. 원격 워커는 `pyproject.toml`의 `[dependency-groups].remote`를 읽어 필요한 패키지만 설치하며, 본체 추론·음성·메모리 의존성 전체를 설치하지 않는다.

아카이브 해시 단위 캐시와 설치 완료 마커를 사용한다. 워커 정상 종료는 세션 권한을 폐기하고 브라우저 리소스를 정리한다. 시작 명령의 인증값은 비밀정보이므로 공유하지 않는다.

## 유효기간·검증·한계

초기 연결 기한은 600초, 연결 상태 확인은 마지막 폴링 기준 180초다. 별도의 10시간 실제 작업 유휴 기한은 작업 발행 또는 인증된 작업 결과에 따라 갱신되며 **단순 heartbeat로 연장되지 않는다**.

Windows·Linux 실기기에서 원격 워커의 종단간 정상 작동을 모두 검증했다는 기록은 없다. Linux의 Chromium 라이브러리, 네트워크·인증, 브라우저 파일 권한과 실제 도구 실행은 대상 환경에서 확인해야 한다. 원격 임시 파일은 호스트의 최종 답변 임시 산출물 정리 생명주기와 자동 연동되지 않는다.

관련 검증 코드는 `tests/test_remote_python_worker.py`, `tests/test_remote_worker_bootstrap.py`, `tests/test_remote_unified_routing.py`, `tests/test_remote_idle_timeout.py`를 참고한다. 테스트 통과만으로 실제 운영체제·네트워크 환경 검증을 대체하지 않는다.

## 원격 도구 타임아웃

원격 워커에서 도구별 실행 제한을 로컬 기본값의 2배로 적용한다. 예를 들어 파일 검색·코드 작업의 60초 제한은 원격에서 120초, 터미널 실행의 120초 제한은 240초로 확장한다. 도구 등록시 타임아웃이 없는 항목은 기존 의미를 유지한다. 서버의 원격 결과 대기 시간은 워커의 최장 기본 실행 시간 및 전송 여유를 고려해 300초로 설정한다. 로컬 도구 타임아웃 및 연결 유효기간은 변경하지 않는다. 타임아웃이 발생하면 오류를 그대로 전달하며 로컬 재실행으로 대체하지 않는다.

## 원격 워커 캐시 자동 정리

Windows `%LOCALAPPDATA%\\MAI\\RemoteWorker`, Linux `${XDG_DATA_HOME:-$HOME/.local/share}/mai/remote-worker`의 설치 완료된 SHA-256 버전 캐시를 대상으로 한다. 워커가 정상적으로 시작할 때 현재 코드 묶음의 사용 시각을 갱신하고, 지난 사용 시각으로부터 30일 이상 지난 **이전 버전**만 정리한다. 워커 실행 중에는 OS 파일 잠금으로 활성 버전을 보호하며 다른 프로세스에서 잠금 중인 캐시는 삭제하지 않는다. 과거 버전처럼 사용 시각 기록이 없는 캐시는 첫 조회 시 현재 시각을 기록하고 즉시 삭제하지 않는다. `.installed-ok`가 없는 부분 설치 디렉터리, 해시 이름이 아닌 폴더, 심볼릭 링크는 자동 삭제 대상으로 간주하지 않는다. 정리 오류는 숨기지 않고 연결 시작 실패로 보고한다. 정상 원격 세션 종료 시 활성 캐시의 마지막 사용 시각도 갱신한다. Python 브라우저 런타임의 공유 캐시나 사용자 파일에는 이 정책을 적용하지 않는다.
