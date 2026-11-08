# MAI dev: Linux 도구 호환성 점검 (2026-11-07)

이 문서는 GitHub 소스 정적 점검 결과다. Linux 장비에서의 전체 실행 성공 판정은 아니다.

| 도구/기능 | 소스 검사 결과 | Linux 실기기 검증 |
|---|---|---|
| terminal_run | `mai/platform/linux.py`: `/bin/sh`, 독립 프로세스 그룹, `killpg`; `shell=powershell` 명시적 거절 | 미실행 |
| 로컬 file_read / file_write | `mai/tools/filesystem.py`: pathlib 기반, PS1 BOM 처리에 OS 조건 있음 | 미실행 |
| code / code_edit | `mai/tools/code.py`: pathlib 기반 파일 작업, 일반 파일 API | 미실행 |
| CSV 집계 | `mai/tools/csv_aggregate.py`: Python 파일/집계 경로 | 미실행 |
| 문서 편집 | `mai/tools/document_edit.py`: tempfile와 Path | 미실행 |
| browser_* | `mai/tools/browser.py`: Playwright Chromium, Linux 브라우저 바이너리 및 시스템 라이브러리 필요 | 미실행 |
| 메인 모델 직접 이미지 입력 | `mai/app/main_model_images.py`, `mai/llm/llamacpp.py`: GGUF/projector 설정에 따라 이미지 바이트 전달; 별도 image_analyze 도구 없음 | 미실행 |
| 원격 is_remote=true | `mai/remote/routing.py`의 `native.invoke` 경로와 Windows PowerShell·Linux shell 워커 시작 스크립트가 구현됨 | Linux 원격 실기기 종단간 검증 **미확인** |
| 음성 | `mai/app/voice.py`의 faster-whisper 및 설치 의존성, 오디오/마이크 환경 | 미실행 |
| 원격 웹 공개 | Tailscale / Cloudflare **호스트별 설치·로그인** 필요 | 미실행 |

## 설치 전 차단 요인

1. Linux에서 `./mai.sh setup`과 `./mai.sh start`를 사용한다. 기존 Linux Ollama 전용 런처는 제거했다.
2. `./mai.sh setup`이 Linux 네이티브 Python·uv·llama.cpp의 해시 검증과 호스트 캐시 설치를 수행한다. Windows 바이너리는 Linux에서 동작하지 않는다.
3. `settings.yaml`의 프로젝트 기준 GGUF 상대경로는 공통 사용 가능하지만, GPU Vulkan 드라이버와 `vulkaninfo`, `libvulkan`, Linux 네이티브 의존성, 실행 가능한 마운트와 SQLite 읽기/쓰기 권한은 대상 호스트별로 확인한다.
4. Linux 원격 대상용 `mai/app/static/remote-agent-linux.sh`와 `mai/remote/python_worker.py`가 추가되어 있다. `is_remote=true`는 서버에 연결된 대상에서 `native.invoke`로 호출된다. 스크립트의 설치 의존성·인증 연결 및 실제 기능 동작은 별도 Linux 호스트에서 검증해야 한다. 단순히 코드가 있다는 이유로 실사용 성공을 보장하지 않는다.
5. `terminal_run` 도구 설명에 Windows CMD/PowerShell 안내가 길게 포함되어 있지만, 런타임 컨텍스트는 POSIX `/bin/sh`를 지정한다. LLM이 Windows 문법을 잘못 선택하는 상황은 실제 테스트가 필요하다.

## Ubuntu 24.04에서 확인할 스모크 테스트

```bash
python3 portable/check-linux-gpu.py --require
bash -n mai.sh
bash -n portable/start-portable-llamacpp-linux.sh
./mai.sh setup
bash portable/start-portable-llamacpp-linux.sh
```

실제 동작을 확인할 때는 웹 로그인 후 `terminal_run`에서 `pwd; uname -a`, 한글 파일 읽기/쓰기, Python 코드 편집, CSV 집계, DOCX 수정, Chromium 웹 접근, Gemma 4 이미지 분석, LFM Verifier JSON 검증, 원격 Windows 작업을 순서대로 시행한다. 테스트 실패는 누락된 OS 의존성·모델 미지원·도구 구현 결함으로 구분해 기록한다.

**결론:** Linux 로컬 도구의 OS 경계와 Linux 원격 워커 시작·실행 경로는 구현되어 있다. 다만 전체 도구·추론·플러그인 및 원격 Linux 연결을 실기기에서 검증한 결과는 확인되지 않았으므로 정상 운용을 인증하지 않는다. [원격 워커 구현](remote-native-worker.md) 참조.
