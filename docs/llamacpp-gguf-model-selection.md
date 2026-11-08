# llama.cpp GGUF 모델 탐색·선택

**현재 백엔드:** llama.cpp 전용. 모델 추가·기본 경로 변경: [설치 안내](../setup.md).

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
* `LLAMACPP_MAIN_GGUF_PATH`는 **기본 선택 모델**이며 탐색 폴더 안에 있어야 한다. 실행기가 등록된 GGUF의 내부 모델 ID를 `MAIN_MODEL` 환경변수에 지정한다. 선택한 같은 모델 ID를 Main·Verifier·기억 추출에 전달한다.
* 파일명은 UI 표시 이름이고, 내부 라우터 ID는 **정규화된 GGUF 경로의 SHA-256 기반 ID**다. 따라서 사용자가 임의의 임시 ID를 입력해 로드할 수 없다.
* 모델 파일 목록은 **프로세스 시작 시 생성된 고정 스냅샷**이다. 파일 추가·삭제 또는 `settings.yaml` 변경을 반영하려면 MAI Portable 서버를 재시작한다. 실행 중 목록과 등록된 라우터가 달라지면 명시적으로 오류를 반환한다.

## Reasoning 설정

`settings.yaml`:

```yaml
LLAMACPP_REASONING_PRESERVE: false
```

- `false`: Windows와 Linux Portable 실행기가 `--no-reasoning-preserve`로 라우터를 시작하고, 자식 모델이 상속하는 `LLAMA_ARG_REASONING_PRESERVE=false`도 설정한다. `true`일 때는 해당하는 활성 옵션을 쓴다. 옵션은 모델 파일이나 llama.cpp 바이너리를 수정하지 않고, 실행 시 전달한다.
- llama.cpp OpenAI 호환 요청에서는 **`reasoning_effort` 필드를 보내지 않는다**. 빈 문자열이 아니라 필드 자체를 생략하므로 채팅 템플릿의 기본 effort를 사용한다.
- reasoning preserve를 비활성화해도 **현재 턴의 Think 생성은 별도로 유지될 수 있다**. 또한 `think=False`인 Verifier/기억 추출에서 이전의 `reasoning_effort=none` 강제값이 제거되므로 모델 기본 reasoning이 작동할 수 있다. 실제 동작은 모델/템플릿으로 검증해야 한다.
- 설정은 Portable 런처에만 적용되므로 바이너리 버전을 올려도 YAML 설정이 유지된다. 새 llama.cpp 버전에서 지원하지 않는 플래그라면 시작 실패를 그대로 보고하며 옵션을 무시하거나 암묵적으로 이전 설정으로 되돌리지 않는다.

## 응답 생성 상한

`settings.yaml`의 `MODEL_MAX_OUTPUT_TOKENS: 32768` 설정은 llama.cpp 채팅 요청의 `max_tokens`에 적용된다. 새 llama.cpp 바이너리로 교체해도 MAI 요청에서 값을 읽어 전달한다. 실행 환경의 `.env`와 환경변수가 지정한 값이 YAML보다 우선한다.

`max_tokens`는 **한 모델 요청에서 생성할 수 있는 전체 출력 토큰 상한**이며, 현재 Think 생성량을 별도로 제한하는 설정이 아니다. `finish_reason="length"`로 종료되면 비록 부분 응답이 있어도 MAI는 완료된 최종 답변이나 정상적인 툴 호출로 인정하지 않고 프로토콜 오류로 보고한다. Verifier는 llama.cpp에서 Main과 동일한 어댑터를 공유하므로 이 제한도 동일하게 적용되고, 기억 추출 전용 어댑터는 이 설정의 적용 대상이 아니다.

## Windows / Linux 실행

공통 실행 명령(Windows Git Bash / Ubuntu 24.04 Bash):

```bash
./mai.sh check
./mai.sh start
```

| OS | 내부 llama.cpp 실행기 |
|---|---|
| Windows | `portable/start-portable-llamacpp-router.ps1` |
| Ubuntu 24.04 | `portable/start-portable-llamacpp-linux.sh` |

두 실행기는 GGUF 목록으로 `--models-preset`을 생성하고 `--models-max 1`로 실행한다. **상주 모델 한 개**만 허용. 별개 프로세스의 VRAM 점유는 제어하지 않음.

설치: `./mai.sh setup` · llama.cpp 바이너리 업데이트: `./mai.sh update`. 기타 OS별 명령: [설치 안내](../setup.md).

## UI 전환 계약

1. 소유자 모델 선택 UI에 등록된 GGUF **파일명**을 표시한다.
2. 선택 변경 시 `POST /models/select` 요청으로 해당 모델 ID를 보낸다.
3. 진행 중인 MAI의 모델·기억 작업과 실행 순서를 맞춘 후, 라우터 `/models/unload`에서 이전 모델의 실제 `unloaded` 상태를 확인한다.
4. 이전 모델이 완전히 내려간 경우에만 `/models/load`를 호출하고 `loaded` 상태를 확인한다.
5. 성공일 때만 UI에서 선택 상태를 확정한다. 오류가 나면 오류를 보이고 원래 선택을 되돌린다. **오류를 다른 모델 재시도나 묵시적 fallback으로 숨기지 않는다.**
6. 이후 그 모델 ID로 생성·검증·기억 사실 추출을 수행한다. 서로 다른 추론 프롬프트를 사용하며 KV 캐시 공유는 보장하지 않는다.

**동일 GGUF를 계속 사용할 때는 강제 언로드·재로드하지 않는다.** Main과 Verifier는 같은 모델 가중치를 사용하되 프롬프트·검증 계약은 분리된다. 비전 지원 여부를 파일명으로 추측하지 않으며, 기본 Ornith용 mmproj를 다른 모델에 자동 적용하지 않는다.

## 검증

```bash
python -m pytest -q tests/test_llamacpp_auto_catalog_and_switch.py tests/test_llamacpp_shared_model_alias.py tests/test_linux_llamacpp_portable.py tests/test_llamacpp_confirmed_unload.py
```

| 검사 | 범위 |
|---|---|
| GGUF 카탈로그 | 직접 자식 파일, 기본 모델, projector 제외 |
| 모델 전환 | 상태 확인, 언로드·로드 순서, 실패 처리 |
| 동일 모델 사용 | Main → Verifier 호출 시 불필요한 재로드 방지 |
| 실제 GPU 추론·VRAM | **실기기 확인 필요** |
