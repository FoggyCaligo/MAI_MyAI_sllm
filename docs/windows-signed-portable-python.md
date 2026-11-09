# Windows 서명된 Portable Python 런타임

Windows MAI는 `portable/portable-runtimes/python` 안의 **Python Software Foundation(PSF) 서명 CPython**을 사용한다. Smart App Control이 서명되지 않은 Windows `python-build-standalone` 실행 파일을 차단하는 문제(CodeIntegrity 3077/3033)를 피하기 위한 경로다. Linux 준비 절차에는 적용되지 않는다.

## 명령과 역할

| 명령 | 동작 |
|---|---|
| `./mai.sh start` | 기존 서명된 Python 실행 환경으로 시작. 설치·업데이트 안 함 |
| `./mai.sh check` | 실행 파일 서명·파일 및 GGUF 카탈로그 확인. GPU 추론은 실행하지 않음 |
| `./mai.sh setup` | 서명된 Python 3.11.9를 재사용하거나 없을 때 공식 도구로 설치 준비. uv·llama.cpp는 별도 버전·해시로 준비 |
| `./mai.sh update` | **llama.cpp만** 업데이트. Python은 변경하지 않음 |
| `./mai.sh update-python -PythonVersion 3.11.9` | 명시적 Python 업데이트. 정확한 3.11.x 패치 버전 지정 필요 |

`setup`과 `update-python`에서만 Python 런타임을 설치할 수 있다. 준비 PC에는 공식 Python 설치 관리자(`pymanager` 또는 `py`)가 필요하지만, 준비된 USB를 옮겨 실행할 때에는 관리자가 필요하지 않다. 설치 관리자 자체가 갱신되더라도 MAI의 고정된 런타임을 자동 업그레이드하지 않는다. MAI·llama.cpp를 종료한 상태에서 준비·갱신한다.

## 서명·기존 런타임 계약

- 기본 패치 버전은 기존 검증된 **3.11.9**다. 이 버전 선택은 호환성 정책이며 최신 3.11 보안 업데이트를 포함한다는 보증이 아니다.
- PSF 서명이 유효한 기존 3.11.9는 설치 과정에서 재사용할 수 있다. 기존 `.mai-portable-source.json`이 다른 배포자를 가리켜도 새 `.mai-signed-python.json`은 현재 확인된 서명된 실행 파일의 **인수(adoption)** 사실을 기록할 뿐 원본 아카이브의 출처를 입증하지 않는다.
- 서명이 없거나 다른 버전이 설치되어 있으면 일반 `setup`은 기존 런타임을 덮어쓰지 않고 실패한다. `./mai.sh setup -PreserveAndReinstallExisting`은 명시적으로 이전 실행 환경을 시각이 붙은 백업으로 보존하고 재설치한다.
- `update-python`은 기존 환경을 백업하고 서버 포트가 점유된 상태에서는 실행을 거절한다. 부동 버전 태그 `3.11` 대신 정확한 패치 버전을 지정한다.
- `python.exe` 단일 파일만 바꾸면 안 된다. DLL·표준 라이브러리·네이티브 확장까지 포함하는 전체 실행 환경을 교체해야 한다.
- **Smart App Control을 비활성화하는 것은 해결 방법이 아니다.** PSF 서명이 다른 외부 확장 모듈까지 모든 Windows 정책에서 승인된다는 보장도 없다.

## 이동·복원 제약

실행기는 `$PSScriptRoot`를 기준으로 파일 위치를 찾으므로 드라이브 문자를 고정하지 않는다. 그러나 다른 Windows PC로 옮기면 CPU 아키텍처, 네이티브 DLL, 패키지 경로, App Control 정책이 달라질 수 있다. Editable 설치에 절대경로가 남았을 수 있으므로 이동 뒤에는 공식 설치 관리자·Portable uv가 있는 환경에서 `setup`을 다시 실행해 재구성한다. 데이터베이스·GGUF·설정·사용자 파일은 Python 설치기가 삭제하지 않는다.
