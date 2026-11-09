# MAI llama.cpp 추론 백엔드와 장기 보존

현재 MAI의 유일한 추론 백엔드는 [llama.cpp](https://github.com/ggml-org/llama.cpp)의 OpenAI 호환 `llama-server`다. Ollama 실행 파일·SDK·어댑터는 사용하지 않는다. 별도 제공자로 실패를 자동 우회하지 않는다.

## 모델과 런타임

- 공식 GGUF 배포처·모델 라이선스·필요한 mmproj/projector·SHA-256을 확인해 파일을 별도로 확보한다.
- Windows Git Bash와 Ubuntu 24.04 Bash에서는 공통으로 `./mai.sh setup`, `./mai.sh check`, `./mai.sh start`를 사용한다. Windows PowerShell은 `.\mai.ps1`을 사용할 수 있다.
- 설치 스크립트는 배포자의 SHA-256을 보유한 런타임 매니페스트를 사용하여 Python·uv·llama.cpp 네이티브 실행 파일을 가져온다. Python 패키지는 `pyproject.toml`을 따른다.
- 설정된 GGUF 폴더의 직접 자식 모델을 시작 시 카탈로그로 등록하고 검증한다. Main·Verifier·기억 추출에 선택된 동일 GGUF를 사용한다. 이는 Verifier 프롬프트의 독립성은 유지하지만 모델 파라미터의 독립성을 제공하지 않는다.
- 라우터는 `--models-max 1`로 작동하며, 모델 교체 시 이전 모델 언로드를 확인하고 다음 모델을 로드한다. 디스크 KV 저장·복원은 하지 않는다.
- 모델과 projector가 지원할 때만 메인 모델에 첨부 이미지 바이트를 직접 전달한다. 별도의 이미지 분석 모델 도구는 없다.

## 런타임 갱신 및 백업

MAI 본체 Git은 소스·설치 코드·문서의 복구를 목표로 하지만 **현재 `dev`의 Git 트리에는 과거 `data/`의 SQLite 파일이 남아 있다.** 이를 별도 저장소 `MAI_MEMORY/`로 경로 변경한 것과 혼동하지 않는다. 이전 추적 기록의 정리는 별도 작업이다. 모델 가중치·OS 전용 바이너리·개인 `.env`·`MAI_MEMORY/*.sqlite3`·감사 로그 및 업로드는 별도 보관해야 한다.

1. MAI 서버와 llama.cpp 라우터를 종료한다.
2. 개인 SQLite는 서비스 중지 후 일관된 스냅샷으로 백업하거나 SQLite 온라인 백업을 사용한다.
3. 기존 런타임 파일·배포 URL·릴리스 버전·SHA-256 매니페스트를 보존한다.
4. 새 릴리스의 배포자 SHA-256으로 검증된 실행 파일을 준비한다. 해시 불일치·기존 설치 영수증 불일치는 오류로 처리한다.
5. Main 도구 호출, Verifier JSON, 기억 추출, 이미지 입력, 모델 전환을 실제로 시험한다.

Windows와 Linux는 GPU 드라이버·런타임 바이너리·실행 권한이 다르다. GGUF와 개인 SQLite를 공유할 수 있어도 동일 바이너리를 양쪽에서 사용할 수는 없다. 128K 컨텍스트 설정은 모델과 GPU의 실제 지원을 보장하지 않는다.

자세한 구현 계약은 [GGUF 모델 선택](llamacpp-gguf-model-selection.md), [Portable 안내](../portable/README.md), [OS 경계](platform-boundary.md)를 참고한다.
