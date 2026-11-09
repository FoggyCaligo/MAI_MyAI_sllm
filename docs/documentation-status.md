# MAI 문서 현행화 점검 기록

**기준:** 2026-11-09의 `dev` 코드 및 현재 작업 브랜치. 문서 내용은 실제 구현을 설명하는 것이고 Windows·Linux·네트워크·GPU에서의 종단간 작동 보증이 아니다. 기존 사용자가 제공한 `706 passed, 4 skipped`는 **이 문서 PR 이전 테스트 결과**이므로 이번 문서 변경의 신규 테스트 결과로 표기하지 않는다.

## 문서별 기준 구현

| 문서 | 확인하는 구현·설정 | 현행화 사항 |
|---|---|---|
| [프로젝트 안내](../README.md) | `mai.sh`, `mai/app/runtime.py` | llama.cpp 전용, GGUF, Memory 별도 저장소, 주요 문서 링크 |
| [설치 안내](../setup.md) | `mai.sh`, `mai.ps1`, `settings.yaml` | 모델 설치·실행, `MAI_MEMORY`, 모델 호출 300초, 기본 검증 OFF |
| [Portable](../portable/README.md) | `portable/` 실행기·카탈로그 | 단일 모델 상주, 운영체제별 런타임, Memory 별도 복제 |
| [실행 구조](architecture.md) | `mai/app/runtime.py`, `mai/agent/loop.py` | 요청 흐름과 요청별 Verifier 활성화 |
| [답변 검증](verifier.md) | `mai/agent/verification.py`, `settings.yaml` | 현재 기본 OFF와 추가 품질 검토 OFF 구분 |
| [장기기억](memory.md) | `mai/memory/`, `mai/app/runtime.py` | 검증 비활성 요청의 저장 흐름, DB 경로 |
| [기억 저장소 분리](memory-repository.md) | `mai/app/server.py`, Portable 실행기, `.gitignore` | `MAI_MEMORY/` 클론과 경로, 기존 Git 추적 DB 경고 |
| [문장 분해기](sentence-breaker.md) | `mai/memory/segmenter.py`, DB 설정 | 누적 DB 기본 경로와 외부 알고리즘 설명 구분 |
| [도구 결과](TOOL_RESULT_PAGINATION.md) | `mai/tools/web.py`, `mai/agent/tool_results.py` | 빈 결과의 명시적 성공 계약, 원문/발췌 구별 |
| [OS 및 인코딩](platform-boundary.md) | `mai/platform/`, `mai/tools/terminal.py` | Git Bash·PowerShell·Linux, UTF-8와 추가 디코딩 후보 |
| [Linux 점검](linux-tool-audit.md) | Linux 실행기 및 `mai/platform/linux.py` | 기본 셸 Bash, 실기기 미검증 표시 |
| [원격 PC 연결](remote-tool-connection.md) | `mai/remote/routing.py`, `agent_client.py` | 원격 도구, 인코딩, 전송·실행 한도 분리 |
| [원격 Native 워커](remote-native-worker.md) | `mai/remote/python_worker.py` | 실제 워커 등록 배수, 동일 도구 재사용 |
| [원격 웹 공개](remote-hosting.md) | `mai/hosting/`, `server.py` | GAIA 시험 서버 비공개 설정과 원격 PC 도구 구분 |
| [도구 타임아웃](host-tool-timeouts.md) | `mai/remote/session.py`, `mai/tools/`, `settings.yaml` | 모델 300초, 로컬 터미널 240초, 원격 480초, 전송 600초 |
| [GGUF 모델 선택](llamacpp-gguf-model-selection.md) | `portable/llamacpp_model_catalog.py`, `mai/llm/` | 모델 전환·요청 한도, 기본 검증 상태 |
| [llama.cpp 보존](llamacpp-portability.md) | Portable 실행기, GGUF 카탈로그 | 경로 전환과 남아 있는 기존 Git 추적 데이터 |
| [모델 타임아웃 진단](llamacpp-timeout-diagnostics.md) | `mai/llm/llamacpp.py`, `mai/llm/models.py` | 300초는 개별 모델 호출의 제한 |
| [프롬프트 캐시·Verifier 비교](prompt-cache-verifier-comparison.md) | `mai/app/server.py`, `mai/app/runtime.py` | 한국어 통일, 검증 기본 OFF |
| [로그인 실패 제한](login-ip-lockout.md) | `mai/app/login_rate_limit.py`, `server.py` | GAIA 로그인 사전 세션 계약 |
| [Windows 서명 Python](windows-signed-portable-python.md) | Portable Python 설치·업데이트 | 한국어 안내, 서명·Smart App Control 제약 |
| [GAIA Benchmark](../benchmark/GAIA%20Benchmark/docs/README.md) | `benchmark/GAIA Benchmark/adapter/*.py`, `docs/*.ps1` | 사전 로그인·router·채점 분모·체크포인트·실패·Linux 제한·격리 수준 |

## GAIA 실행 계약에서 주의할 점

- Windows의 `setup.ps1`은 데이터 동의·인증을 대신하지 않는다. 사용자가 직접 Hugging Face에서 접근 권한을 확보해야 한다.
- `start_benchmark_server.ps1`은 llama.cpp router를 자동 시작하지 않는다. 별도로 `127.0.0.1:8080` 연결 상태를 확인해야 한다.
- 시험 서버는 `8010`과 별도 네 개의 SQLite DB 경로를 사용하며, **파일 시스템 접근 권한·음성 프로필·업로드까지 완전히 격리하는 것은 아니다**.
- `run_gaia.py`는 `GET /login/session` 이후 사전 세션 헤더를 포함한 `POST /login`을 호출한다.
- 평가 단계의 `completed`는 답변 형식 및 작업 완료이고 GAIA 정답을 뜻하지 않는다. `score_gaia.py`는 `strip().casefold()` 기반의 **비공식 완전 일치 채점**이며 누락 문제 대비 전체 수행률을 별도 계산한다.
- 이미 기록된 실패 문제도 체크포인트 재개 때 건너뛴다. 재시험은 별도 결과 폴더에 수행한다.
- Linux용 `setup.sh`는 있지만 전용 평가 서버 시작 스크립트는 없다. Windows PowerShell 시작 명령을 Linux 실행 가능으로 소개하지 않는다.

## 남겨 둔 구분과 확인 과제

1. **실기기 검증:** Windows Git Bash/PowerShell, Ubuntu Linux, 원격 워커, llama.cpp GPU·모델 전환 및 GAIA 첨부파일별 실행은 실제 환경에서 확인해야 한다. 문서 수정만으로 테스트가 실행된 것으로 간주하지 않는다.
2. **기존 Git 추적 DB:** 현재 본체 Git에 과거 `data/*.sqlite3`가 추적되어 있다. 새 `MAI_MEMORY/` 경로와 별개이며 자동 삭제·추적 해제·Git 기록 재작성은 이 문서 PR의 범위가 아니다.
3. **GAIA 격리·채점:** 공식 평가 규칙·완전한 OS 샌드박스는 현재 어댑터에 구현되어 있지 않다. 정답을 프롬프트에 전달하지 않는 것과 모델의 파일 접근을 OS 차원에서 차단하는 것은 다르다.
4. **문서 보관:** `docs/.old/`는 과거 기술 설명, `docs/history/`는 이전 인수인계 기록이다. 현행 설정값과 상충하더라도 이력을 고쳐 현재 사실처럼 만들지 않는다.
5. **적용 범위:** 이번 작업은 문서 현행화로, GAIA 어댑터·원격 타임아웃·보안 권한·SQLite 추적 정책 등 실제 실행 동작은 변경하지 않는다.

문서 검토 기준: 코드에 없는 기능을 완료로 쓰지 않고, 실패를 성공으로 간주하지 않으며, 단순 문자열 패턴으로 오류·의미를 판정하는 동작을 권장하지 않는다.
