# MAI — 개인 컴퓨터용 로컬 AI 비서

MAI는 사용자의 컴퓨터에서 로컬 언어 모델로 실행되는 범용 AI 비서다. 대화하면서 기억을 검색하고, 웹·파일·문서·코드·터미널 도구로 작업한다. 기본 추론 백엔드는 Ollama이며 llama.cpp를 명시적으로 선택할 수 있다.

## 시작하기

- [일반 설치·계정·설정·문제 해결](setup.md)
- [외장 SSD Portable 준비·실행·백업](portable/README.md)
- [실행 구조와 개발 계약](docs/architecture.md)

일반 설치를 마친 뒤 프로젝트 루트에서 실행한다.

```powershell
.\.venv\Scripts\Activate.ps1
python scripts/run_server.py
```

브라우저에서 `http://127.0.0.1:8000`에 접속한다. 외부 접속은 별도 Tailscale Funnel 설정이 필요하다. 파일 수정·삭제와 터미널 명령은 실제 PC에 반영되므로 계정 권한과 작업 대상을 확인한다.

## 주요 기능

| 기능 | 지원 범위 |
|---|---|
| 장기기억 | 사용자별 사실·개념 그래프를 SQLite에 저장하고 필요한 기억을 검색 |
| 웹·시장 정보 | 검색, 페이지 본문 읽기, 브라우저 탐색, 한국 상장주식 시세·평가 정보 조회 |
| 파일·코드 | 폴더 탐색, 텍스트 읽기·쓰기·복사·이동·삭제, 코드 검색·구조 분석·정확한 문자열 편집 |
| 문서 | PDF·DOCX·XLSX·CSV·PPTX 읽기와 지원되는 위치의 기존 문서 수정 |
| 터미널·원격 PC | 소유자 권한으로 명령 실행, 연결된 원격 PC에 같은 도구 사용 |
| 이미지·음성 | 설정된 이미지 모델로 분석, 음성 인식·화자 비교, 브라우저 음성 합성 |
| 웹 앱 | 로그인, 대화 기록, 진행 상황, 업로드, 파일 다운로드 링크 |

문서 편집은 DOCX 문단·표 셀, XLSX 셀, PPTX 도형 텍스트, CSV 기존 셀, PDF 페이지 회전·삭제를 지원한다. 새 문서 생성이나 PDF 본문 직접 수정은 `document_edit`의 지원 범위가 아니다. 코드 구조 분석은 Python과 JavaScript·TypeScript·TSX·C·C++·Java·Go·Rust·Ruby를 지원한다.

## 문서 안내

| 내용 | 기준 문서 |
|---|---|
| 에이전트·권한·설정·개발 점검 | [실행 구조](docs/architecture.md) |
| 기억 저장·검색·정정의 범위 | [장기기억](docs/memory.md) |
| 모델이 선택한 발췌·원문 재열람 | [도구 결과 전달](docs/TOOL_RESULT_PAGINATION.md) |
| 수치·사실·작업 결과 검증 | [답변 검증](docs/verifier.md) |
| llama.cpp·모델 장기 보존 | [로컬 추론 백엔드](docs/llamacpp-portability.md) |
| 원격 PC 연결 | [원격 연결](docs/remote-powershell-connection.md) |
| 로그인 실패 제한 | [로그인 제한](docs/login-ip-lockout.md) |
| 정적 코드 점검 | [참조 감사](docs/REFERENCE_AUDIT.md) |
| 문장 분해기 알고리즘 | [문장 분해기](docs/sentence-breaker.md) |
| 과거 작업·장애 기록 | [2026-11-04 인계 기록](docs/history/MAI_CODEX_HANDOFF_2026-11-04.md) |

## 라이선스

최초 개발자: 신재용. 저작권 표기: `Copyright (C) 2026 신재용`.

저작권자가 라이선스할 권한을 가진 코드에는 **AGPL-3.0-only**가 적용된다. 수정·배포 및 네트워크 제공에 따른 소스 공개 의무 등은 [LICENSE](LICENSE)를 따른다. 제3자 코드의 권리와 라이선스는 각각의 고지를 따른다. [NOTICE](NOTICE) 참조.
