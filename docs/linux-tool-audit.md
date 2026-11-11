# Linux 도구·런타임 점검

**대상:** Ubuntu 24.04 x86-64 · llama.cpp · Bash. **판정:** 코드 경로 확인 / Linux 실기기 종단간 검증 미확인.

이전 점검 기록: [.old/linux-tool-audit.md](.old/linux-tool-audit.md). 설치 안내: [setup.md](../setup.md).

## 1. 준비 상태 확인

| 항목 | 요구사항·경로 | 확인 |
|---|---|---|
| 운영체제 | Ubuntu 24.04 x86-64 | `uname -m`, `cat /etc/os-release` |
| 기본 명령 | `python3`, `git`, `curl`, `tar`, `sha256sum`, `flock`, `zstd` | `./mai.sh setup` |
| GPU | 하드웨어 Vulkan GPU·드라이버·`vulkaninfo` | `check-linux-gpu.py --require` |
| Python·uv·llama.cpp | Linux 네이티브 실행 파일, SHA-256 검증 | `./mai.sh setup` |
| Python 패키지 | `pyproject.toml` 기본 + `dev` | `./mai.sh setup` |
| 브라우저 | Playwright Chromium + Linux 시스템 라이브러리 | 실제 브라우저 실행 |
| GGUF·projector | `portable/portable-models/gguf/` | `./mai.sh check` |
| 로그인 | Owner 자격 증명이 있는 `.env` | 웹 로그인 |

- 설치 경로: `${XDG_CACHE_HOME:-$HOME/.cache}/mai-portable/<프로젝트 경로 해시>/`
- GGUF·설정·SQLite: 프로젝트 경로 유지. **Windows `.exe`를 Linux에서 실행하지 않음.**
- GPU 드라이버 변경은 기본 설치 대상 아님. 필요 시 `./mai.sh setup --install-vulkan`을 **명시적으로** 실행.
- `check` 통과 ≠ 모델의 GPU 추론 성공.

## 2. 설치·실행 확인

프로젝트 루트에서 순서대로 실행:

```bash
bash -n mai.sh
bash -n portable/start-portable-llamacpp-linux.sh
python3 portable/check-linux-gpu.py --require
./mai.sh setup
./mai.sh check
./mai.sh start
```

| 단계 | 성공 기준 | 실패 시 확인 |
|---|---|---|
| Shell 구문 | `bash -n` 종료 코드 0 | 스크립트 문법 |
| GPU 사전 검사 | Vulkan 물리 GPU 인식 | `vulkaninfo`, ICD·드라이버·`/dev/dri` 권한 |
| setup | Python·uv·llama.cpp 설치·해시 검증, Chromium 준비 | 매니페스트, 배포 ZIP, 필수 시스템 명령 |
| check | `.env`, GGUF 카탈로그, 네이티브 실행 파일 확인 | 기본 모델 경로·projector·GGUF 헤더 |
| start | llama.cpp 라우터 준비 후 MAI 서버 실행 | 포트 8080/8000 충돌, VRAM, 모델 로딩, 실행 로그 |
| 웹 UI | `http://127.0.0.1:8000`에서 로그인 | 서버 로그·브라우저 접근 |

Linux 실행기는 `portable/start-portable-llamacpp-linux.sh`. 모델 카탈로그를 기반으로 `--models-preset`, `--models-max 1` 사용. Main·Verifier·기억 추출에 선택된 **동일 GGUF** 적용.

## 3. 로컬 도구 점검

| 기능 | 구현 경로 | Linux 확인 방법 |
|---|---|---|
| 터미널 | `mai/platform/linux.py`, `mai/tools/terminal.py` | `terminal_run`: `pwd; uname -a`; 기본 Bash |
| 파일 | `mai/tools/filesystem.py` | 한글 이름 파일 읽기·쓰기·삭제 권한 확인 |
| 코드 | `mai/tools/code.py` | Python 파일 분석·편집, 저장 내용 재확인 |
| CSV | `mai/tools/csv_aggregate.py` | 필터·그룹·집계 결과 재계산 |
| 문서 | `mai/tools/document_edit.py` | DOCX 수정·재열기·원본 보존 |
| 웹·브라우저 | `mai/tools/browser.py` 외 웹 도구 | Chromium 실행·페이지 열기·내용 확인 |
| 계산·시간·시세 | `mai/tools/calculator.py`, `time.py`, `external.py` | 실제 도구 호출과 결과 확인 |
| 이미지 | `mai/app/main_model_images.py` | 지원 GGUF·projector에서 이미지 직접 입력 |
| 음성 | `mai/app/voice.py` | 마이크·오디오 환경 및 실제 인식 확인 |

**터미널 계약:** Linux는 설치된 `bash` 사용. `shell=powershell`은 지원하지 않으며 오류로 처리. Windows 셸 명령을 자동 변환하지 않음.

**이미지 계약:** 별도 Ollama `image_analyze` 없음. `LLAMACPP_MAIN_VISION`과 실제 GGUF/projector 호환성 필요.

**Verifier 계약:** `MAI_VERIFIER_ENABLED`가 켜진 요청에서 기존 LFM 별도 모델이 아닌 현재 선택된 Main GGUF로 별도 검증 요청 수행. 기본 배포 값은 `false`다. 모델 상주는 유지할 수 있지만 KV Cache 공유는 보장하지 않음.

## 4. 원격 워커·외부 접속

| 기능 | 현재 구현 | 추가 검증 |
|---|---|---|
| 원격 Linux 도구 | `mai/app/static/remote-agent-linux.sh`, `mai/remote/python_worker.py` | Linux 대상 PC에서 실제 연결·명령 실행 |
| 원격 실행 | `mai/remote/routing.py`, `at_remote_pc=true` | 원격/로컬 경로 구분, 실패 시 로컬 대체 없음 |
| 원격 웹 공개 | Tailscale Funnel / Cloudflare | 호스트 설치·로그인·실제 외부 HTTPS 접속 |

연결 절차: [원격 도구 연결](remote-tool-connection.md). 웹 공개와 원격 Native 워커는 서로 다른 기능.

## 5. 회귀 테스트·판정 기록

```bash
python -m pytest -q tests/test_linux_llamacpp_portable.py tests/test_platform_adapters.py tests/test_remote_python_worker.py tests/test_remote_worker_bootstrap.py
python -m pytest -q
```

테스트 실행에는 **MAI 의존성이 설치된 Python** 사용. 시스템 `python`에 의존성이 없으면 그 상태는 환경 오류이며 테스트 통과로 취급하지 않음.

| 구분 | 현재 판정 |
|---|---|
| Linux 실행·도구 경로 | 소스 구현 확인 |
| Shell 구문·단위 테스트 | 이 문서 작업에서 실행하지 않음 |
| Vulkan GPU 모델 추론 | 실기기 검증 미확인 |
| Chromium·음성·이미지 | 실기기 검증 미확인 |
| Linux 원격 워커 종단간 | 실기기 검증 미확인 |

**기록 원칙:** 성공 / 실패 / 미실행을 구분. 필수 의존성 오류를 건너뛰거나 정상으로 표시하지 않음.

**문자 인코딩:** Linux Bash 자식 프로세스는 `C.UTF-8`, Python은 UTF-8 출력으로 구성하고, 터미널 수신 바이트는 UTF-8 우선 후 명시된 한국어 구형 인코딩을 순서대로 시도한다. [운영체제 경계](platform-boundary.md)를 참고한다.

**지속 DB:** 기본 경로는 별도 Git 저장소 `MAI_MEMORY/`이며, 본체 Git에 남은 과거 `data/` 파일의 정리는 별도 작업이다.
