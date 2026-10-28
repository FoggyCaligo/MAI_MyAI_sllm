# MAI 로컬 추론 백엔드와 장기 보존

현재 MAI의 기본 텍스트 추론 엔진은 Ollama임. 선택적으로 [llama.cpp 공식 저장소](https://github.com/ggml-org/llama.cpp)의 `llama-server`를 별도 프로세스로 실행해 사용할 수 있음. **두 실행 경로 간 자동 fallback은 없음.** 어느 쪽이든 실패는 오류로 드러남.

## llama.cpp 실행 예시 (Windows)

1. [공식 릴리스](https://github.com/ggml-org/llama.cpp/releases)에서 사용 중인 GPU/OS에 맞는 `llama-server` 릴리스 자산을 다운로드함. AMD GPU 사용 시 Windows Vulkan 빌드가 후보이나, 드라이버와 빌드 호환성을 실기기에서 확인해야 함.
2. **별도로 라이선스를 확인한 GGUF 모델 파일**을 로컬에 보관함. Ollama `blobs` 디렉터리에 있는 파일을 곧바로 독립 GGUF 모델이라고 가정하지 않음. 내보낼 수 있는 형태인지 검증하고 원본 배포처를 확인할 것.
3. `llama-server.exe -m C:\\models\\model.gguf --alias local-model --host 127.0.0.1 --port 8080 --jinja` 실행. `--jinja`는 모델의 chat template 및 function calling 확인을 위한 옵션. 템플릿이 도구 호출을 지원하지 않으면 사용할 수 없음.
4. MAI의 로컬 `.env`에서 `LLM_BACKEND=llamacpp`, `LLAMACPP_HOST=http://127.0.0.1:8080`, `MAIN_MODEL=local-model`을 지정하고 `python run_server.py`로 실행함.
5. THINK 출력, `tool_manual`, `memory_search`, 수치 검증, Verifier 재시도, 사실 추출이 정상 동작하는지 실제 모델로 검증할 것. OpenAI 호환 API라고 해도 모델의 도구 호출·추론 출력이 동등하다는 보장은 없음. 별도의 이미지 인식 기능은 기존 Ollama `VISION_MODEL`을 사용함.

llama.cpp 설정을 제거하고 기존 설정(`LLM_BACKEND=ollama`)으로 되돌리면 기존 Ollama 실행 경로로 복귀함.

## 재설치 가능성과 백업은 다름

Git 저장소에는 **MAI 소스코드·검증 스크립트·설치 방법**을 보관함. 대형 모델 가중치, 운영체제 전용 실행 파일, 개인 `.env`, `memory.sqlite3`, `chat.sqlite3`, `log.sqlite3`는 Git에 포함하지 않는 것을 권장함. 모델과 실행 파일의 공개 URL은 나중에 폐쇄될 수 있으므로 **영구 다운로드 가능성은 보장할 수 없음.**

- 공식 배포처의 **특정 릴리스 버전과 자산명**, 라이선스, 원본 URL, SHA-256 해시를 백업 목록에 기록할 것.
- 미리 구해둔 파일은 외장 SSD 또는 독립 저장소에 **오프라인 원본 사본**을 보관할 것. 백업 뒤 별도 환경에서 복원 검증할 것.
- 다운로드 시 `python scripts/fetch_verified_artifact.py <URL> <SHA256> <DESTINATION>`를 사용하면 지정한 SHA-256과 다를 경우 오류로 중단됨. SHA-256은 신뢰 가능한 별도 경로에서 확보해야 함. URL이 장래에 사라지면 다운로드는 실패하며 다른 모델로 자동 대체되지 않음.
- Ollama 현재 설치본의 `models` 전체 폴더(기본 경로 `%USERPROFILE%\\.ollama\\models`) 및 Ollama 설치 프로그램도 별도 백업할 것.
- 개인 데이터베이스는 MAI 종료 후 **일관된 상태의 SQLite 백업**을 수행할 것. 실행 중 DB 파일만 복사하는 방식은 WAL 상태를 누락할 수 있음.
- Python 의존성은 현재 일부 버전 범위 및 Git 브랜치(예: Sentence_Breaker@main)에 묶여 있음. 따라서 저장소만으로 미래에 완전한 동일 환경 복원은 아직 보장되지 않음. 추후 의존성 lock/배포 파일 아카이브가 필요함.

## 호환성 범위

이 변경은 **텍스트 채팅 런타임의 llama.cpp 연결 경로**를 추가하는 작업임. llama.cpp 실행 파일과 GGUF 가중치를 MAI가 자동 설치하거나, 현재 Ornith를 llama.cpp에서 즉시 실행할 수 있음을 보증하는 변경은 아님. 실제 모델별 회귀 테스트 결과를 보고 지원 범위를 확정할 것.
