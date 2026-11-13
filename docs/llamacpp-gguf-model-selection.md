# llama.cpp GGUF 모델 탐색·선택

**현재 백엔드:** llama.cpp 전용. 모델 추가·기본 경로 변경: [설치 안내](../setup.md).

## 모델 목록과 기본값

모델별로 독립 폴더를 사용한다. 폴더 이름은 자유롭지만 각 폴더 안에는 **모델 GGUF 정확히 1개**와 **mmproj GGUF 0개 또는 1개**만 허용한다. 모델의 Q4/Q8과 mmproj의 BF16/Q8 이름이 서로 달라도 같은 폴더면 연결된다.

```text
portable/portable-models/gguf/
├── Ornith-1.5-9B/
│   ├── Ornith-1.5-9B-Q4_K_M.gguf
│   └── mmproj-Ornith-1.5-9B-BF16.gguf
├── Gemma-4-E4B-Q4/
│   ├── gemma-4-E4B-it-Q4_K_M.gguf
│   └── mmproj-gemma-4-E4B-it-BF16.gguf
├── Gemma-4-E4B-Q8/
│   ├── gemma-4-E4B-it-Q8_0.gguf
│   └── mmproj-gemma-4-E4B-it-Q8_0.gguf
└── LFM2.5-2.6B/
    └── LFM2.5-2.6B-Q4_K_M.gguf
```

`settings.yaml`의 기본 모델 경로는 다음과 같다.

```yaml
LLAMACPP_GGUF_DIRECTORY: "portable/portable-models/gguf"
LLAMACPP_MAIN_GGUF_PATH: "portable/portable-models/gguf/Ornith-1.5-9B/Ornith-1.5-9B-Q4_K_M.gguf"
```

- 설정한 상위 폴더의 **직접 자식 폴더**만 모델 설치 디렉터리로 판단한다. `.cache`와 같은 숨김 폴더는 제외한다. 상위 폴더에 남은 평면 GGUF는 새 카탈로그 대상이 아니다.
- 모델 GGUF는 일반 `.gguf` 파일, 프로젝터 GGUF는 `mmproj-` 또는 `mmproj_` 접두사가 붙은 `.gguf` 파일로 구분한다. 이는 파일 역할을 구분하는 규칙이지, 모델 호환성을 파일명으로 추측하는 규칙이 아니다.
- 모델 하나에 프로젝터가 여러 개이거나, 같은 폴더에 모델 GGUF가 여러 개이면 **자동 선택 없이 실패**한다. 파일이 누락되거나 GGUF 헤더가 올바르지 않아도 시작을 실패로 처리한다.
- 별도의 `LLAMACPP_MAIN_MMPROJ_PATH` 또는 모델별 JSON 연결표는 사용하지 않는다. 이전 설정값이 남아 있으면 무시하지 않고 명시적인 오류를 반환한다.
- 선택한 GGUF의 경로를 해시한 내부 ID가 UI에 등록되며, Main·Verifier·기억 추출은 같은 모델 ID를 사용한다.
- 모델 목록과 프리셋은 **MAI 시작 시 스냅샷**으로 생성된다. 폴더를 추가하거나 파일을 이동한 다음에는 `./mai.sh check` 후 재시작한다. 선택 UI에서 모델을 바꾸면 같은 폴더의 프로젝터를 포함한 프리셋으로 전환한다.
- **기존 평면 폴더에서의 마이그레이션:** 모든 GGUF를 대응하는 모델 전용 폴더로 이동하고 `LLAMACPP_MAIN_GGUF_PATH`를 갱신해야 한다. 구체적인 안전 이전 예시는 [설치 안내](../setup.md)를 참고한다.

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

**동일 GGUF를 계속 사용할 때는 강제 언로드·재로드하지 않는다.** Main과 Verifier는 같은 모델 가중치를 사용하되 프롬프트·검증 계약은 분리된다. 이미지 첨부는 선택한 모델 폴더에 mmproj가 연결된 경우에만 전달한다. 다른 폴더의 mmproj를 대신 연결하지 않으며 실제 호환성은 llama.cpp에서 검증한다.

**시간 제한:** `MODEL_REQUEST_TIMEOUT_SECONDS: 300`은 각 모델 API 요청의 기본 제한이다. 요청 전체, 도구 실행 또는 GAIA 문제별 제한과 혼동하지 않는다. Verifier는 배포 설정 `MAI_VERIFIER_ENABLED: false`에서 기본적으로 꺼져 있다.

## 검증

```bash
python -m pytest -q tests/test_llamacpp_auto_catalog_and_switch.py tests/test_llamacpp_shared_model_alias.py tests/test_linux_llamacpp_portable.py tests/test_llamacpp_confirmed_unload.py
```

| 검사 | 범위 |
|---|---|
| GGUF 카탈로그 | 모델별 직접 자식 폴더, 1개 모델/최대 1개 projector, 기본 모델 경로 검증 |
| 모델 전환 | 상태 확인, 언로드·로드 순서, 실패 처리 |
| 동일 모델 사용 | Main → Verifier 호출 시 불필요한 재로드 방지 |
| 실제 GPU 추론·VRAM | **실기기 확인 필요** |

## 메모리 보호 설정 (Windows/Linux)

`settings.yaml`의 기본값:

```yaml
LLAMACPP_MIN_FREE_VRAM_MIB: 1024
MAI_MIN_AVAILABLE_RAM_MIB: 6144
LLAMACPP_CONTEXT_SIZE: 65536
```

- `LLAMACPP_MIN_FREE_VRAM_MIB`: 모델 로딩 전에 router `/models`의 GPU별 `memory.max.devices[].free`와 모델/컨텍스트/연산 예상 사용량을 비교한다. projector 등의 미계측 할당을 위한 1024 MiB의 **추가 내부 여유분**을 차감한다. 예상 잔여량이 설정값보다 작거나 측정치가 없으면 모델 로딩을 거부한다. 측정은 **router가 수행했던 스냅샷**으로, 현재 순간의 GPU 가용량을 재측정하는 기능이나 GPU 하드 메모리 한도가 아니다. 이미 로딩된 모델에는 재적용하지 않는다.
- `MAI_MIN_AVAILABLE_RAM_MIB`: 매 신규 모델 작업 전 Windows `GlobalMemoryStatusEx.ullAvailPhys` 또는 Linux `/proc/meminfo:MemAvailable`를 확인한다. 임계값 미만이거나 계측 실패 시 해당 요청을 거부한다. 시스템 전체의 RAM 사용 상한, 강제 종료, 메모리 부족 해소 기능이 아니다.
- `LLAMACPP_CONTEXT_SIZE`: 모델 preset의 컨텍스트 상한. KV cache 배치에 영향을 주며 기본 64K.
- 실행 중 2초 간격으로 가용 물리 RAM을 `mai/mcp_tools/mcp_memorizer/MAI_MEMORY/gpu_ram_safety.jsonl`에 기록한다(설정된 `LOG_DB_PATH`와 같은 디렉터리). GPU는 모델 로딩 전 추정치를 기록한다. 강제 전원 차단 시 가장 마지막 기록 일부가 손실될 수 있다.
- 이미 진행 중인 GPU 호출은 RAM 부족 감지만으로 즉시 멈출 수 있다고 보장하지 않는다. 주기 샘플은 진단 목적으로 저장하고 다음 요청은 시작 전에 다시 확인한다.
- 설정 변경 시 MAI 재시작 필요. Windows launcher에서 명시적으로 `-ContextSize`를 지정하는 경우 런처 인자가 우선한다.
- **반복적인 시스템 전원 차단은 메모리 보호만으로 해결되지 않을 수 있다.** 불안정한 PC에서 장시간 벤치마크로 검증하지 말고 전력/메모리/드라이버 원인을 따로 진단한다.
