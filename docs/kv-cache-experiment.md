# dev2: 단일 모델 + 역할별 KV 캐시 교체 실험

이 브랜치는 `dev2`를 기반으로 한 단일 모델 실험이다. 메인 모델과 숫자/사실 검증은 **동일한 llama.cpp GGUF 가중치 한 벌**을 순차적으로 사용한다. 모델 크기는 9B나 12B 등으로 고정하지 않는다.

## 동작

- **main**: 모델에 도구와 대화 문맥을 전달해 임시답변 생성
- **numeric**: 숫자 검사에서 별도의 언어모델 검토가 필요한 경우 숫자 전용 프롬프트와 체크포인트로 전환
- **final**: 최종 사실검증 프롬프트/체크포인트로 전환
- **REJECT**: 다음 main 호출 시 기존 main KV 캐시를 복원하고 도구/피드백을 추가해 계속 실행
- **ACCEPT/오류**: 실행별 체크포인트 파일을 삭제하려 시도 (전원 차단으로 남은 파일은 수동 정리)

메인/숫자/사실 검증의 구조화 응답 및 실제 검증 프롬프트 자체는 변경하지 않는다. 세 역할이 같은 모델 가중치를 공유해도 **서로 다른 프롬프트와 KV 상태**를 갖는다. 매번 입력 Prefix가 동일해야 기존 KV가 활용되며 다른 부분은 다시 Prefill한다. 호출 횟수나 연산량이 자동으로 감소하지는 않는다.

## 구현 경계

- `MAI_EXPERIMENTAL_SLOT_CACHE=true` 및 `LLM_BACKEND=llamacpp`일 때만 활성화한다.
- 브라우저 UI의 모델 선택값이 해당 턴의 메인·숫자·최종검증에 모두 적용된다. 오래된 `.env`의 `VERIFIER_MODEL`은 무시된다.
- llama.cpp 모델 목록은 서버에 **이미 적재된 단일 GGUF alias**만 노출한다. 브라우저 UI에서 임의의 다른 GGUF를 선택해 서버에 즉시 적재하는 기능은 아니다. 다른 GGUF를 시험할 때는 llama.cpp 서버를 종료하고 해당 GGUF로 재시작한다.
- 같은 GGUF alias로 단일 llama.cpp 서버를 `--parallel 1`, `--slot-save-path`, `--cache-prompt`과 함께 실행한다.
- 요청의 `id_slot=0`, `cache_prompt=true`를 강제한다. 실행 시작에 슬롯 0만 있고 유휴 상태인지, `/v1/models` alias가 일치하는지 확인한다.
- MAI 사용자 턴 사이에서는 비동기 잠금을 사용해 순차 실행한다. 이전 메모리 후처리가 남아 있으면 기다린다.
- 서버 외부에서 같은 포트로 요청을 보내면 독점이 깨질 수 있다. 실험 중 다른 클라이언트가 llama.cpp 8080 포트에 연결되지 않게 한다.
- 실행별 캐시 파일은 민감한 문맥의 표현을 포함할 수 있어 git에 포함하지 않는다. 임의 삭제 전에 서버 요청이 종료됐는지 확인한다.
- Verifier 컨텍스트를 인위적으로 32K로 제한하는 기능은 이 브랜치에 포함하지 않는다.

## Portable 실행

Windows에서는 이전에 준비한 llama.cpp Vulkan 런타임 및 해당 모델 GGUF가 필요하다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/start-portable.ps1 -Backend llamacpp -GgufPath ./portable/portable-models/gguf/model.gguf -ModelAlias local-model -SlotCache
```

이 실행기는 `MAI_EXPERIMENTAL_SLOT_CACHE`, `MAI_SLOT_CACHE_DIR`, `MAIN_MODEL`, `VERIFIER_MODEL`을 단일 모델로 설정한다. 일반 `dev2`의 `LLM_BACKEND=ollama` 실행은 기존대로 동작하며 캐시 슬롯 기능은 사용하지 않는다.

일반 PC 직접 실행에서는 llama.cpp를 별도로 **동일한 인수로 시작**하고, 다음 환경변수를 지정해야 한다.

```powershell
$env:LLM_BACKEND = "llamacpp"
$env:MAI_EXPERIMENTAL_SLOT_CACHE = "true"
$env:MAI_SLOT_CACHE_DIR = "C:\\MAI-KV-CACHE"
$env:MAIN_MODEL = "local-model"
$env:VERIFIER_MODEL = "local-model"
python run_server.py
```

## 검증

```bash
python -m pytest -q tests/test_slot_cache.py tests/test_role_cache_runtime.py
python -m pytest -q
```

현재 구현은 코드 및 가짜 HTTP 서버 수준의 실험으로, 실제 Windows Vulkan llama.cpp 및 12B 실행에서 정확도·지연·메모리 안정성이 확인된 것은 아니다. 이전 PC 강제 종료 원인도 아직 확인되지 않았다. **먼저 9B·짧은 질문으로 시험한 후** 최대 VRAM, CPU/RAM, 저장/복원 시간, 입력 Prefix 재사용률을 기록하는 편이 안전하다.
