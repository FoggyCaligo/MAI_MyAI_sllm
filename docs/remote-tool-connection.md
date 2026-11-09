# 원격 PC 도구 연결 (Windows / Linux)

MAI가 **다른 컴퓨터의 도구**를 실행하는 기능. 원격 웹 공개(Tailscale Funnel·Cloudflare)와 별개.

## 1. 연결 준비

| 항목 | Windows 워커 | Linux 워커 |
|---|---|---|
| 실행 셸 | PowerShell | POSIX shell (`sh`) |
| Python | Python 3.12 설치 또는 `py` 사용. 없으면 `winget` 설치 시도 | Python **3.11 이상**, `python3 -m venv` |
| 다운로드·검증 | PowerShell `Invoke-WebRequest`, `Get-FileHash` | `curl`, `unzip`, `sha256sum` |
| Python 의존성 | `pyproject.toml`의 `remote` 그룹 | 동일 |
| 브라우저 도구 | Playwright Chromium 설치 | Playwright Chromium 및 필요한 OS 라이브러리 |
| 연결 대상 | **HTTPS MAI 서버** | **HTTPS MAI 서버** |

- MAI의 **Owner** 계정 필요. 체험 계정은 원격 관리 불가.
- 대상 PC의 파일·프로세스에 접근할 수 있는 **해당 셸 사용자 권한**으로 실행. 관리자 권한 자동 상승 없음.
- 대상 PC에서 MAI 서버로 **HTTPS 아웃바운드 연결** 필요. 별도 인바운드 포트 개방 불필요.
- 연결 전 [원격 웹 공개 설정](remote-hosting.md)과 원격 PC 연결을 구분할 것. 공개 URL 사용 시 로그인·접속 권한을 점검.

## 2. 연결 방법

1. MAI 웹 UI에 **Owner 로그인**.
2. 로그아웃 옆 **원격 연결** 클릭.
3. 대상 PC의 OS에서 **Windows** 또는 **Linux** 선택.
4. 화면에 생성된 **일회용 연결 명령** 복사.
5. **대상 PC**의 PowerShell(Windows) 또는 터미널(Linux)에서 실행.
6. UI에 **원격 연결됨** 표시 확인.
7. MAI에 원격 PC 작업 요청. 필요한 Native 도구는 `tool_manual`로 활성화한 다음 `is_remote=true`로 호출.

**연결 명령 직접 작성 금지:** 서버 주소, 세션 ID, 임시 인증 키가 포함되므로 UI에서 발급한 명령 그대로 사용한다. 재사용·공유 금지.

### 실행 구조

```text
MAI 웹 UI (Owner)
  └─ 원격 연결 → Windows / Linux 명령 발급
       └─ 대상 PC에서 실행
            ├─ Windows: remote-agent.ps1
            └─ Linux: remote-agent-linux.sh
                 ↓
             HTTPS 부트스트랩
                 ↓
             소스 ZIP + SHA-256 검증
                 ↓
             의존성 해시별 공유 Python 가상환경 + Chromium
                 ↓
             mai.remote.agent_client
                 ↕ HTTPS 폴링·결과
             MAI 서버 (native.invoke)
```

## 3. 설치·실행 세부사항

| 단계 | Windows | Linux |
|---|---|---|
| 부트스트랩 파일 | `mai/app/static/remote-agent.ps1` | `mai/app/static/remote-agent-linux.sh` |
| 인증된 소스 | `/remote/agent/{session_id}/bootstrap` | 동일 |
| 무결성 | 응답 `X-MAI-Bundle-SHA256`와 다운로드 ZIP 비교 | 동일 |
| 설치 캐시 | `%LOCALAPPDATA%\MAI\RemoteWorker\<SHA256>\` | `${XDG_DATA_HOME:-$HOME/.local/share}/mai/remote-worker/<SHA256>/` |
| 의존성 | `pyproject.toml:remote` 그룹 | 동일 |
| 실행 프로세스 | `python -m mai.remote.agent_client` | 동일 |
| `terminal_run` 기본 셸 | Git Bash | Bash |

소스 묶음에는 MAI 서버가 사용 중인 `mai/**/*.py`와 `pyproject.toml`이 포함된다. **모델 GGUF·llama.cpp·서버의 기억 DB는 원격에 설치하지 않음.**

소스와 공유 가상환경 각각의 설치 완료 마커 `.installed-ok`가 없으면 미완료로 판단한다. 원격 의존성 그룹과 Python 실행 버전·플랫폼이 같으면 다른 MAI 소스 ZIP에서도 공유 가상환경을 재사용한다. 새 가상환경이 필요하면 먼저 완전히 설치·검증하고, 원격 워커 실행 시 더 이상 사용하지 않는 이전 공유 가상환경을 잠금 확인 후 제거한다. 다만 설치와 프로세스 시작 사이의 간격을 보호하기 위해 마지막 사용 후 10분이 지나지 않은 환경은 정리하지 않는다. 새 환경 설치가 실패하면 기존 정상 환경은 유지되며 실패가 드러난다.\n\n소스 ZIP은 동일한 바이트로 재생성되도록 아카이브 메타데이터를 정규화한다. 이전 정상 소스 묶음은 **마지막 사용 기준 30일** 보존한 다음 다음 워커 실행 시 정리한다. `.installed-ok`가 없는 이전 소스 폴더는 24시간 이상 경과하고 현재 사용 중이지 않을 때 정리한다. 설치 중인 환경과 연결된 실행 프로세스가 잠근 캐시는 정리하지 않는다. 기존 버전에서 만든 폴더 내부의 `.venv`는 이전 소스 버전과 함께 30일 보존될 수 있으므로, 업그레이드 직후 디스크 사용량이 바로 최저치가 되지는 않는다. pip 캐시와 Playwright 전역 브라우저 캐시는 별도 관리 대상이다. 패키지 설치·브라우저 설치 오류는 실패로 드러낸다.

## 4. 원격 도구 사용

| 인수 | 실행 위치 |
|---|---|
| `is_remote=false` 또는 생략 | MAI 서버 컴퓨터 |
| `is_remote=true` | Owner가 연결한 원격 Windows/Linux 워커 |

예시 — Linux 원격 PC의 파일 읽기(`file_read` 활성화 후):

```json
{"path":"/home/user/notes.txt","is_remote":true}
```

- **지원:** 파일·코드·CSV·문서 작업, `terminal_run`, 계산·시간·웹·시장 정보, 브라우저 등 원격 워커 등록 Native 도구.
- **서버 전용:** `memory_search`, `tool_result_read`, `file_download_link` 등 서버 상태에 종속된 도구.
- **이미지 분석:** 기존 원격 `image_analyze` 도구 없음. 첨부 이미지 인식은 MAI 서버의 메인 llama.cpp 모델이 담당.
- **원격 세션 식별:** MAI 서버가 로그인한 Owner의 연결을 선택. 모델이 세션 ID를 도구 인수로 지정하지 않음.
- **오류:** 원격 세션 부재·연결 끊김·미지원 도구는 **실패**. 로컬 PC로 자동 전환하지 않음.
- **셸:** 대상 OS의 문법 사용. Windows 명령을 Linux shell 문법으로 임의 변환하지 않음.

자세한 구현: [Native 워커 계약](remote-native-worker.md).

**문자 인코딩:** 로컬·원격 워커는 같은 `mai/tools/terminal.py`, `mai/tools/filesystem.py`를 사용한다. 현재 터미널 자식 프로세스는 Windows Git Bash/CMD/PowerShell과 Ubuntu Bash에서 UTF-8 출력을 우선하며, 디코딩 오류 시 `utf-8-sig`, `cp949`, `euc-kr`, `iso2022_kr` 순으로 엄격히 시도한다. 파일 읽기는 기본 `utf-8-sig`, 쓰기는 기본 `utf-8`이다. Windows `.ps1` 기본 저장은 PowerShell 5.1을 위한 UTF-8 BOM을 사용한다. 선택한 터미널 디코딩은 `stdout_encoding`·`stderr_encoding`에 보고한다. 자세한 사항은 [운영체제 경계](platform-boundary.md)를 참고한다.

## 원격 도구 타임아웃

원격 터미널 명령의 내부 실행 한도는 로컬 240초 / 원격 480초다. 파일·코드 도구는 로컬 120초 / 원격 240초이며, 웹·시장 정보 도구는 로컬 60초 / 원격 120초다. **브라우저 도구는 현재 원격 워커 등록 경로에서 2배가 두 번 적용되어 로컬 기본 제한의 4배다.** 따라서 모든 원격 도구가 정확히 2배라는 단순한 설명은 현재 구현과 다르다.

원격 세션 서버가 도구 결과를 기다리는 전송 한도는 **600초**다. 이는 도구 자체의 480초 제한을 감싸는 상한이며, 작업별 최대 600초 실행을 허용한다는 뜻은 아니다. 시간 초과나 실패는 오류로 기록하고 성공으로 간주하지 않는다. 기존 세션 최초 연결 10분, 폴링 180초, 유휴 10시간 정책과는 별개다.

## 5. 연결 종료·유효기간

| 조건 | 값·동작 |
|---|---|
| 발급 후 최초 연결 제한 | **10분** |
| 연결 유지 확인 | 마지막 폴링 기준 **180초** |
| 실제 작업 없는 상태 | **10시간** 경과 시 세션 만료 |
| 단순 폴링 | 10시간 작업 유휴 기한 연장 **안 함** |
| 원격 도구 요청·인증된 결과 | 작업 유휴 기한 갱신 |
| UI **연결 해제** | 서버에서 해당 세션 권한 폐기 |
| 워커 종료 (`Ctrl+C` 등) | 새 작업 중단, 정상 종료 시 세션 회수 시도 |
| 비정상 종료·네트워크 장애 | 세션 회수 실패 가능. 상태 확인 기한 만료 시 사용 불가 |

**주의:** 세션 폐기는 새로운 명령 실행 권한을 막지만, 원격 PC에서 이미 시작된 별도 프로세스를 항상 종료한다는 뜻은 아니다. 브라우저 로그인에서 로그아웃해도 이미 연결된 Owner 원격 워커 세션은 자동 해제되지 않을 수 있으므로, 필요하면 **연결 해제**를 별도로 실행한다.

## 6. 보안·점검

- 발급 명령에 임시 키 포함. 터미널 기록·클립보드·화면 공유에 노출 주의.
- **신뢰하는 본인 소유 PC**에서만 실행. 대상 PC 계정 권한 범위 내의 파일 수정·삭제·명령 실행이 가능.
- Windows/Linux 실기기 종단간 동작은 코드 및 단위 테스트만으로 보장되지 않음.

검사 명령(개발 환경):

```bash
python -m pytest -q tests/test_remote_python_worker.py tests/test_remote_worker_bootstrap.py tests/test_remote_unified_routing.py tests/test_remote_idle_timeout.py
```

관련 파일: `mai/remote/routing.py`, `mai/remote/session.py`, `mai/remote/agent_client.py`, `mai/remote/python_worker.py`. 이전 원본: [.old/remote-powershell-connection.md](.old/remote-powershell-connection.md).
