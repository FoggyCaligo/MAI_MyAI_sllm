# llama.cpp Portable — GGUF 폴더 탐색 및 모델 선택 실험

이 문서는 `experiment/llamacpp-auto-gguf-ui-switch-20261108` 브랜치용이다.
Ollama 모델 검색·설정이나 Main/Verifier의 검증 계약은 변경하지 않는다.

## 모델 목록과 기본값

`settings.yaml`:

```yaml
LLAMACPP_GGUF_DIRECTORY: "portable/portable-models/gguf"
LLAMACPP_MAIN_GGUF_PATH: "portable/portable-models/gguf/Ornith-1.5-9B-Q4_K_M.gguf"
LLAMACPP_MAIN_MMPROJ_PATH: "portable/portable-models/gguf/mmproj-Ornith-1.5-9B-BF16.gguf"
LLAMACPP_MAIN_VISION: true
```

* `LLAMACPP_GGUF_DIRECTORY` 아래의 **직접 자식 `.gguf` 파일**을 실행 시 읽고, GGUF 매직 헤더를 확인한다. 디렉터리가 없거나 잘못된 파일이 있으면 시작을 실패로 처리한다.
* 설정에 **명시된** `LLAMACPP_MAIN_MMPROJ_PATH` 및 `LLAMACPP_VISION_MMPROJ_PATH`는 모델 선택 목록에서 제외한다. **다른 projector 파일의 역할을 이름으로 추측하지 않는다.** 추가 projector를 폴더에 함께 보관할 경우 별도 설정에 경로를 명시하거나 일반 모델 폴더와 분리해야 한다.
* `LLAMACPP_MAIN_GGUF_PATH`는 **기본 선택 모델**이며 탐색 폴더 안에 있어야 한다. `MAIN_MODEL` / `VERIFIER_MODEL`의 Ollama 태그는 llama.cpp 라우터 시작 시 생성된 모델 ID로 덮어쓴다.
* 파일명은 UI 표시 이름이고, 내부 라우터 ID는 **정규화된 GGUF 경로의 SHA-256 기반 ID**다. 따라서 사용자가 임의의 임시 ID를 입력해 로드할 수 없다.
* 모델 파일 목록은 **프로세스 시작 시 생성된 고정 스냅샷**이다. 파일 추가·삭제 또는 `settings.yaml` 변경을 반영하려면 MAI Portable 서버를 재시작한다. 실행 중 목록과 등록된 라우터가 달라지면 명시적으로 오류를 반환한다.

## Windows / Linux 실행

Windows:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\portable\start-portable-llamacpp-router.ps1
```

Linux (기존 Ubuntu Portable 계약 필요):
```bash
bash portable/start-portable-llamacpp-linux.sh
```

두 스크립트 모두 발견된 GGUF에 대한 `--models-preset`을 만들어 라우터에 등록한다. `--models-max 1`을 유지하므로 llama.cpp 라우터는 한번에 모델 하나만 상주시킨다. 다른 프로세스(예: 따로 실행한 Ollama)의 VRAM 점유는 이 설정으로 제어하지 못한다.

## UI 전환 계약

1. 소유자 모델 선택 UI에 등록된 GGUF **파일명**을 표시한다.
2. 선택 변경 시 `POST /models/select` 요청으로 해당 모델 ID를 보낸다.
3. 진행 중인 MAI의 모델·기억 작업과 실행 순서를 맞춘 후, 라우터 `/models/unload`에서 이전 모델의 실제 `unloaded` 상태를 확인한다.
4. 이전 모델이 완전히 내려간 경우에만 `/models/load`를 호출하고 `loaded` 상태를 확인한다.
5. 성공일 때만 UI에서 선택 상태를 확정한다. 오류가 나면 오류를 보이고 원래 선택을 되돌린다. **오류를 다른 모델 재시도나 묵시적 fallback으로 숨기지 않는다.**
6. 이후 그 모델 ID로 생성·검증·기억 사실 추출을 수행한다. 서로 다른 추론 프롬프트를 사용하며 KV 캐시 공유는 보장하지 않는다.

같은 GGUF를 선택한 상태에서는 재로드하지 않는다. Verifier 독립성은 동일 모델 검증으로 인해 감소할 수 있다. 다른 선택 모델의 비전 지원 여부는 파일명으로 판단하지 않으며, 기본 Ornith에 지정한 mmproj를 모든 모델에 자동 적용하지 않는다.

## 검증

`pytest -q tests/test_llamacpp_auto_catalog_and_switch.py tests/test_llamacpp_shared_model_alias.py tests/test_linux_llamacpp_portable.py tests/test_llamacpp_confirmed_unload.py`

실제 Windows PowerShell, Linux 실행 및 VRAM/프로세스 실측 테스트는 별도로 수행해야 한다.
