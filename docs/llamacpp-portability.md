# dev2: 단일 GGUF와 llama.cpp 장기 보존

추론 엔진은 Ollama가 아니라 **독립 프로젝트 llama.cpp**의 `llama-server`이다.
모델 가중치는 하나의 GGUF 파일이다. 모델 파일과 엔진은 개별 보관·업그레이드한다.

- 모델 선택: `portable/portable-models/gguf/active.gguf` 또는 시작 스크립트의 `-GgufPath` 경로
- 모델 별칭: `settings.yaml`의 `MAIN_MODEL`
- 실행 엔진: `portable/portable-runtimes/llamacpp/`
- 검증: 같은 GGUF로 숫자·최종 사실 검증, 메인 역할 캐시는 REJECT 후 복원
- 프롬프트 캐시: `portable/portable-slot-cache/`, 저장 파일은 사용 뒤 정리
- 배포 업그레이드: 공식 llama.cpp Vulkan 릴리스 URL·배포 SHA256을 새 매니페스트에 고정하고 `prepare-portable.ps1 -PreserveAndReinstallExisting` 실행

이전 버전 실행 파일은 백업되며 .env·기억 DB·GGUF는 별도로 보존된다.
모델·엔진의 라이선스와 해당 OS·GPU 드라이버 호환성을 확인한다.
Ollama 앱의 업데이트는 llama.cpp를 자동으로 업데이트하지 않는다.

구체적인 절차는 [Portable README](../portable/README.md)를 따른다.
