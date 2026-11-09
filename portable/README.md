# MAI Portable — Windows / Linux llama.cpp 전용

MAI의 Portable 기본 실행은 llama.cpp 라우터를 사용한다. 기존 Ollama용 시작기, 모델 복사기, 별도 준비 스크립트는 폐기됐다. 현재 설치와 실행은 저장소 루트에서 수행한다.

## 명령

| 작업 | Windows Git Bash / Linux Bash | Windows PowerShell | Windows CMD |
|---|---|---|---|
| 최초 설치 | `./mai.sh setup` | `.\mai.ps1 setup` | `mai.cmd setup` |
| 환경 점검 | `./mai.sh check` | `.\mai.ps1 check` | `mai.cmd check` |
| llama.cpp 업데이트 | `./mai.sh update` | `.\\mai.ps1 update` | `mai.cmd update` |
| 서버 시작 | `./mai.sh start` | `.\mai.ps1 start` | `mai.cmd start` |

`./mai`는 제공하지 않는다. 루트의 `mai/`는 Python 패키지 디렉터리다. `check`는 파일과 카탈로그 상태만 점검하며, GPU 추론·VRAM 안정성·브라우저 접근성은 검사하지 않는다.

## 환경별 구성

```text
MAI/
  mai.sh, mai.ps1, mai.cmd
  pyproject.toml, settings.yaml, .env
  portable/
    portable-sources.json           # Windows 릴리스 URL·SHA-256
    portable-sources-linux.json     # Linux 릴리스 URL·SHA-256
    portable-runtimes/              # Windows Python·uv·llama.cpp
    portable-models/gguf/           # 공통 GGUF·projector
    portable-downloads/             # 보존된 공식 아카이브
    resolve_portable_sources.py
    resolve_portable_sources_linux.py
    start-portable-llamacpp-router.ps1
    start-portable-llamacpp-linux.sh
    llamacpp_model_catalog.py
    check_llamacpp_catalog.py
    portable_backup.py
    verify_portable_backup.py
  MAI_MEMORY/              # 별도 Git 저장소: SQLite 기억·대화·감사 기록
```

Linux는 Ubuntu 24.04 x86-64 호스트에서 실행하며 네이티브 Python·uv·llama.cpp는 `~/.cache/mai-portable/<프로젝트 경로 해시>/` 또는 `XDG_CACHE_HOME`에 준비한다. SSD에 있는 Windows `.exe`를 Linux에서 실행하지 않는다. Linux에는 `python3`, `git`, `curl`, `tar`, `sha256sum`, `flock`, `zstd` 및 Vulkan 드라이버 등 호스트 의존성이 필요하다.

## 설치

`./mai.sh setup`은 운영체제별 매니페스트가 없으면 생성하고, 공식 릴리스의 배포자 SHA-256을 확인해 Python·uv·llama.cpp 바이너리를 설치한다. 기존 다운로드 파일은 해시가 일치할 때만 재사용한다. 파일이 이미 설치되어 있는데 영수증이나 해시가 맞지 않으면 오류로 종료하며 기존 파일을 임의로 덮어쓰지 않는다.

Python 패키지는 **`pyproject.toml`**의 기본·`dev` 의존성을 설치한다. 별도 요구사항 파일은 사용하지 않는다. Playwright Chromium을 별도로 다운로드한다. 기존 `.env`는 유지하고, 없을 때 Owner 자격 증명을 입력받아 생성한다.

사용자 모델 파일은 자동 다운로드되지 않는다. `settings.yaml`에 선언된 GGUF 기본 파일과 대응 projector가 존재해야 한다. 모델 배포처의 라이선스·파일명·SHA-256을 별도 확인한다.

## llama.cpp 업데이트

MAI를 종료한 후 `./mai.sh update`로 공식 SHA-256 검증 및 신규 실행 파일 테스트를 거쳐 llama.cpp만 교체한다. 이전 런타임과 매니페스트는 백업해 두며, Python·uv·GGUF·SQLite는 변경하지 않는다. 업데이트 뒤에는 `./mai.sh check`와 `./mai.sh start`를 실행한다.

## 추론 및 모델 전환

Windows 실행기는 `portable/start-portable-llamacpp-router.ps1`, Linux는 `portable/start-portable-llamacpp-linux.sh`다. 두 실행기는 `LLAMACPP_GGUF_DIRECTORY`의 실제 파일을 검증해 모델 목록을 만든다. 해당 프로세스에서 한 번에 **모델 한 개만 상주**시킨다.

Main·Verifier·기억 추출은 UI에서 선택한 GGUF를 공통 사용하되 서로 다른 프롬프트·구조화 응답 계약을 따른다. 모델 전환은 기존 모델의 언로드 완료를 확인한 후 로드하며, 실패를 다른 모델로 자동 대체하지 않는다. Main의 KV를 디스크에 저장하거나 Verifier 이후 복원하지 않는다. 필요한 대화·툴 이력은 MAI의 원문 저장·메시지 구성 구조를 사용한다.

Windows 기본 `-ContextSize`와 Linux `ctx-size`는 **131072**다. 모델의 실제 문맥 학습 길이, GPU 할당, 응답 품질과 속도는 환경별로 다르다. Windows는 실행기에 다른 `-ContextSize`를 명시할 수 있고 Linux는 런처 기본 프리셋을 수정해야 한다.

## 백업과 운영상 주의

`portable/portable_backup.py`와 `verify_portable_backup.py`로 백업 및 무결성을 확인한다. 개인 `.env`, GGUF, 실행 바이너리, SQLite DB와 업로드 파일은 공개 저장소에서 제외한다. 데이터베이스는 서비스 종료 후 백업하거나 SQLite 온라인 백업을 사용한다.

Windows와 Linux에서 동일 SSD를 사용하더라도 GPU 드라이버·실행 권한·NTFS 마운트·Chromium 시스템 라이브러리·Tailscale/Cloudflare 로그인 상태는 자동 이전되지 않는다. Linux 원격 도구 대상은 [Native 원격 워커](../docs/remote-native-worker.md)의 별도 세션·설치 경로다.

## 회귀 테스트

```bash
python -m pytest -q tests/test_portable_short_cli.py tests/test_linux_llamacpp_portable.py tests/test_llamacpp_auto_catalog_and_switch.py tests/test_llamacpp_confirmed_unload.py tests/test_remote_worker_bootstrap.py
python -m pytest -q
```

Portable Python에서 `python -m pytest`를 실행해야 설치된 실제 패키지 조합을 확인할 수 있다. 테스트가 통과해도 실제 모델 추론·GPU 오프로딩·원격 Windows/Linux 네트워크 동작의 성공을 보장하지 않는다.
