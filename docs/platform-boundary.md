# MAI 운영체제 경계 — Windows / Linux

MAI의 Agent·Verifier·Memory·도구 계약은 호스트 운영체제와 분리되어 있다. OS별 명령 실행은 `mai/platform/`이 담당하며, 실제 실행 파일·드라이버·패키지는 Windows와 Linux에서 별도로 준비한다.

## 실행 경계

```text
Agent / Verifier / Memory
  ├─ MAI 호스트 Native 도구
  └─ Owner 원격 Native 워커 (is_remote=true)
       └─ terminal_run → current_platform()
            ├─ Windows: Git Bash(기본) / CMD / PowerShell, UTF-8, Windows 프로세스 그룹
            └─ Linux: Bash(기본), POSIX 세션 및 프로세스 그룹
```

`terminal_run`의 `shell`은 선택값이며 생략하면 `bash`다. Windows에서는 Git for Windows의 `bash.exe`를 명시적인 경로에서 찾아 실행하며, 없는 경우 WSL의 `bash.exe`를 대체 사용하지 않고 오류로 처리한다. 특수 설치 경로는 `MAI_GIT_BASH_PATH`로 지정할 수 있다. Windows 전용 CMD와 PowerShell은 각각 `shell=cmd`, `shell=powershell`로 명시한다. Linux에서는 설치된 `bash`를 기본으로 사용하며 CMD/PowerShell 선택은 명시적으로 실패한다. 현재 MAI 플랫폼 계층은 macOS 실행을 지원하지 않으므로 이 변경으로 macOS 지원이 추가되는 것은 아니다.

운영체제 감지는 **명령을 실행하는 실제 컴퓨터**에서 수행한다. 브라우저 위치나 요청 문자열로 운영체제를 추정하지 않는다. 미지원 운영체제와 셸 조합은 명시적으로 실패하며 Linux/Windows로 임의 변환하지 않는다.

`mai/platform/base.py`, `windows.py`, `linux.py`는 터미널 실행, 스크립트 작업 공간, 인코딩, 프로세스 종료 방식을 분리한다. 기존 `_powershell_command`, `_windows_cmd_args` 등 호환 함수는 유지한다. `pathlib`·`shutil` 기반 일반 파일 도구를 Windows/Linux별로 복제하지 않는다.

## Portable 바이너리

Windows Git Bash와 Ubuntu 24.04 x86-64는 모두 `./mai.sh setup`, `./mai.sh check`, `./mai.sh start`로 진입한다. Windows는 `mai.ps1` 내부 설치 함수와 `portable/start-portable-llamacpp-router.ps1`, Linux는 `mai.sh`의 호스트 캐시 설치 함수와 `portable/start-portable-llamacpp-linux.sh`를 사용한다. OS별 Python·uv·llama.cpp 바이너리는 서로 호환되지 않는다.

GGUF 가중치·설정 파일·SQLite 데이터는 두 환경에서 공유할 수 있지만 Windows `.exe`와 GPU 드라이버는 Linux에서 그대로 실행할 수 없다. NTFS에 Linux 가상환경을 설치하는 방식도 지원하지 않는다. SQLite를 복사·이관할 때는 MAI를 종료하거나 SQLite 온라인 백업 기능으로 일관성을 확보한다.

## 원격 컴퓨터

Owner 원격 PC 작업은 `mai/remote/routing.py`의 `native.invoke` 경로를 사용한다. Windows PowerShell 부트스트랩과 Linux shell 부트스트랩(`mai/app/static/remote-agent-linux.sh`)이 현재 소스에 존재한다. 원격 세션이 없거나 미지원 도구일 때는 로컬로 대신 실행하지 않는다.

초기 연결 기한은 600초, 연결 상태 확인 기한은 180초다. 별도 10시간 실제 작업 유휴 기한은 원격 작업 요청 또는 그 결과로 갱신하며, heartbeat만으로 연장하지 않는다. 만료되면 서버의 원격 명령 권한을 폐기하지만 대상 PC에서 이미 실행된 일반 프로그램까지 모두 종료한다는 뜻은 아니다.

연결·지원 도구·설치 의존성은 [원격 Native 워커](remote-native-worker.md) 문서를 따른다. Linux 원격 워커의 소스 구현과 실제 Linux 장비에서의 전체 통합 시험은 구분한다.

## Git과 데이터 백업

Git에는 소스·설정 예시·빌드 스크립트·민감하지 않은 매니페스트·문서만 기록한다. `portable/portable-runtimes/`, `portable/portable-models/`, `portable/portable-slot-cache/`, `.env` 및 개인 메모리·대화·감사 DB를 커밋하지 않는다. GGUF를 배포할 때도 모델 라이선스 및 해시를 확인한다.

Git만으로 모델·실행 바이너리·개인 데이터까지 장기 복원할 수는 없다. 운영체제에 맞는 파일, 모델 가중치, 잠긴 버전·해시 목록 및 독립된 암호화 백업을 별도로 보관해야 한다.

## 테스트와 실기기 검증

```bash
python -m pytest -q tests/test_platform_adapters.py tests/test_windows_shell_contract.py tests/test_windows_terminal_encoding.py tests/test_terminal_evidence_contract.py tests/test_remote_model_context.py
python -m pytest -q tests/test_remote_idle_timeout.py tests/test_remote_python_worker.py tests/test_remote_worker_bootstrap.py
python -m pytest -q
```

테스트가 통과해도 GPU offload, Vulkan 드라이버, Playwright Chromium, 오디오 장치, 웹 외부 공개 및 원격 Linux 네트워크를 실제 장비에서 검증했다는 뜻은 아니다. 테스트 수집 오류와 필수 의존성 누락은 정상 통과로 취급하지 않는다.

## 터미널과 파일 도구의 텍스트 인코딩 계약

- Windows Git Bash, CMD, PowerShell 및 Linux Bash에서 MAI가 시작하는 터미널 자식 프로세스는 UTF-8 텍스트를 출력하도록 환경변수를 명시한다. Windows CMD는 `chcp 65001`, PowerShell 5.1은 콘솔 출력 인코딩을 사용한다. Linux Bash는 지원 OS인 Ubuntu의 `C.UTF-8` 로케일을 사용한다.
- 터미널은 stdout/stderr의 바이트를 우선 `utf-8-sig`로 읽는다. 실패할 때만 `cp949`, `euc-kr`, `iso2022_kr`을 순서대로 각각 strict 디코딩으로 재시도한다. 선택한 디코딩은 `stdout_encoding`, `stderr_encoding` 필드에 표시한다. 전부 실패하면 `UnicodeError`를 발생시키며 `replace`나 `ignore`로 오류를 숨기지 않는다.
- 외부 프로그램이 UTF-8 외의 인코딩을 출력할 가능성은 여전히 있다. 어떤 바이트열은 잘못된 인코딩에서도 유효한 문자가 될 수 있으므로, 자동 선택의 의미적 정확성까지 보장하지는 않는다. 필요하면 반환된 디코딩 방식과 원본 프로그램 설정을 확인한다.
- 일반 텍스트 파일은 `file_write` 기본 UTF-8, `file_read` 기본 `utf-8-sig`다. Windows PowerShell 5.1용 `.ps1` 파일을 기본값으로 작성할 때에는 UTF-8 BOM을 유지한다. 기존 CP949 파일 등은 파일 도구의 `encoding` 인자로 명시적으로 지정한다. 기존 파일을 자동 재인코딩하지 않는다.
- 원격 Windows/Linux 워커도 플랫폼별 터미널·파일 도구를 통해 같은 정책을 적용한다. 전송 JSON은 별도의 텍스트 계약을 따른다.
