# MAI — 로컬 llama.cpp 기반 개인 AI 비서

MAI는 로컬 llama.cpp 추론 서버에 연결해 대화, 도구 실행, 장기기억 및 답변 검증을 수행하는 개인 AI 에이전트다. 현재 런타임은 **llama.cpp 전용**이며 Ollama 어댑터·서버·SDK·모델 매니페스트를 사용하지 않는다. 모델 파일은 GGUF로 관리한다.

## 실행 및 설치

저장소 루트에서 Git Bash(Windows) 또는 Bash(Ubuntu 24.04 x86-64)를 사용한다.

```bash
./mai.sh setup
./mai.sh check
./mai.sh start
```

Windows PowerShell에서는 `./mai.ps1 setup`, `./mai.ps1 check`, `./mai.ps1 start`, CMD에서는 `mai.cmd`를 사용할 수 있다. 저장소 안의 `mai/`는 Python 패키지 디렉터리이므로 `./mai` 실행 파일은 존재하지 않는다.

`setup`은 공식 배포자 SHA-256으로 고정한 Python·uv·llama.cpp 런타임을 준비하고 `pyproject.toml`의 본체·개발 의존성을 설치한다. 첫 설치 시 Owner 로그인 정보를 요청해 `.env`를 만들며 기존 파일은 덮어쓰지 않는다. 지속 DB의 기본 경로는 `./mai/mcp_tools/mcp_memorizer/MAI_MEMORY/`이며, 폴더나 DB가 없으면 최초 실행 시 자동 생성된다. 기존 `.env`에서 DB 경로를 변경했다면 설정값을 확인한다. Linux 실행 파일과 Python 환경은 Linux 호스트 캐시에 설치된다. **기본 Ornith Q8 GGUF와 대응 BF16 projector는 `setup`으로 SHA-256을 확인해 설치한다.** 다른 모델과 projector는 별도로 준비해야 한다.

`check`는 필수 파일과 GGUF 카탈로그를 확인하지만 GPU 추론 성공을 보장하지 않는다. `start`는 llama.cpp의 단일 상주 모델 라우터를 실행한 뒤 MAI 웹 서버를 시작한다. 기본 웹 주소는 `http://127.0.0.1:8000`이다.

MAI 장기기억의 그래프·검색·추출 구현은 **내장 `mai/memory/`**에 있으며, 모델에는 네이티브 `memory_search`만 노출한다. 별도 `MCP_memorizer`는 현재 MAI 런타임 의존성이 아니다. 분해 알고리즘은 [Sentence_Breaker](https://github.com/FoggyCaligo/Sentence_Breaker/blob/dev/docs/algorithm.md)를 사용한다. 상세 내용은 [MAI 기억 구조](docs/memory.md) 참조.

## 기억·대화 데이터 백업

에이전트의 장기기억과 대화 기록은 기본적으로 저장소 루트의 `mai/mcp_tools/mcp_memorizer/MAI_MEMORY/` 폴더에 **SQLite (`.sqlite3`) 파일**로 보관된다. 기억을 백업하거나 다른 컴퓨터로 옮기려면 **MAI를 완전히 종료한 뒤 `mai/mcp_tools/mcp_memorizer/MAI_MEMORY/` 폴더 전체를 복사**하면 된다. `memory.sqlite3`는 장기기억, `chat.sqlite3`는 대화 기록, `log.sqlite3`는 실행 로그를 저장한다. `sentence_breaker.sqlite3`는 문장 분리용 데이터다.

실행 중에는 SQLite의 `-wal`·`-shm` 파일이 존재할 수 있으므로 안전한 파일 백업을 위해 종료 후 복사하는 것을 권장한다. 환경변수로 DB 경로를 변경했다면 `mai/mcp_tools/mcp_memorizer/MAI_MEMORY/` 대신 해당 경로의 파일도 백업해야 한다. **개인 기억·대화 DB는 공개 Git 저장소에 커밋하지 않는다.**

## 핵심 구성

| 구성 | 위치·역할 |
|---|---|
| 메인·Verifier·기억 추론 | `mai/llm/llamacpp.py`, `llamacpp_router.py`; 선택한 GGUF 공통 사용 |
| 에이전트 실행·도구 호출 | `mai/agent/` |
| 사실·수치 검증 | `mai/agent/verification.py` |
| 기억과 검색 | `mai/memory/`, SQLite와 FTS5 |
| 로컬·원격 Native 도구 | `mai/tools/`, `mai/remote/`; Owner는 `switch_workspace(target="client")`로 인증된 워커를 선택 |
| 공개 웹 서버 | `mai/app/`; Tailscale Funnel/Cloudflare는 선택형 별도 기능 |
| Portable 준비·검증 | `mai.sh`, `mai.ps1`, `portable/` |

일반 파일·코드·문서·웹·시장 정보·계산·터미널·브라우저 작업 및 지원 모델의 직접 이미지 입력을 사용할 수 있다. 원격 도구는 인증된 Windows/Linux 워커에서 실행하며 세션이 없으면 로컬로 대체하지 않는다.

## 문서

- [설치·설정·테스트](setup.md)
- [음성 입력: Web Speech·로컬 Whisper STT](docs/voice-stt.md)
- [Portable 구조·백업](portable/README.md)
- [실행 구조](docs/architecture.md)
- [답변 검증](docs/verifier.md)
- [장기기억](docs/memory.md)
- [별도 MAI_MEMORY Git 저장소·이전](docs/memory-repository.md)
- [GAIA Benchmark 실행·평가](benchmark/GAIA%20Benchmark/docs/README.md)
- [타임아웃 정책](docs/host-tool-timeouts.md)
- [도구 원문·페이지네이션](docs/TOOL_RESULT_PAGINATION.md)
- [GGUF 선택과 라우터](docs/llamacpp-gguf-model-selection.md)
- [원격 PC 연결 방법 (Windows / Linux)](docs/remote-tool-connection.md)
- [원격 Native 워커 구현 계약](docs/remote-native-worker.md)
- [운영체제·문자 인코딩](docs/platform-boundary.md)
- [전체 문서 현행화 점검 기록](docs/documentation-status.md)

## 권한과 라이선스

파일 변경과 터미널 명령은 실제 컴퓨터에 반영된다. Owner 접근 자격 증명과 원격 세션 키, 개인 DB·모델 파일은 공개 저장소에 올리지 않는다. 과거 `data/`에 이미 Git 추적된 SQLite 파일은 별도 정리 대상이며 `.gitignore`만으로 Git 기록에서 삭제되지는 않는다.

최초 개발자: 신재용. `Copyright (C) 2026 신재용`. 저작권자가 라이선스할 권한을 가진 코드는 **AGPL-3.0-only**를 따른다. [LICENSE](LICENSE), [NOTICE](NOTICE) 참조.

## Windows EXE 배포 폴더

별도 배포 프로젝트는 [`MAI_light_instant_runner/README.md`](%EB%B0%B0%ED%8F%AC/README.md)에 있다.
Windows + .NET 8 SDK에서 `powershell -ExecutionPolicy Bypass -File ./MAI_light_instant_runner/build.ps1`을 실행하면
`MAI_light_instant_runner/MAI/MAI.exe`와 기존 MAI 소스·실행 스크립트를 배포 구조로 조립한다.
Portable 런타임은 `-IncludeRuntime`, GGUF 파일은 `-IncludeModels` 옵션을 사용해야 포함된다.
개인 데이터(.env, MAI_MEMORY, 업로드)는 절대 기본 배포에 포함하지 않는다.
GitHub Actions의 `Windows MAI EXE Distribution` 워크플로에서도 실행기를 빌드하여 결과물을 제공한다.

현재 모델이 사용하는 툴은 **네이티브 툴**이다. MCP 서버 카탈로그는 로딩하지 않는다. `tool_manual`로 `terminal_run`, `document_read`, `memory_search`, `switch_workspace` 등 필요한 툴을 활성화한다. 파일 목록·검색·이동·삭제 및 계산을 위한 기존 개별 툴 10개는 제거되어 `terminal_run`으로 대체한다. 계산·분석용 임시 파일은 호스트 `./tmp`에 저장하며 최종 답변 후 비동기 기억 후처리가 끝나면 폴더 내부만 정리한다(폴더 자체는 유지). 원격 client의 임시 파일 자동 정리는 지원하지 않는다.
