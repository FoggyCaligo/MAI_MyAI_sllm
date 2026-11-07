# dev2 — 단일 GGUF llama.cpp KV 캐시 교체

dev2는 더 이상 옵트인 다중 백엔드 실험이 아니다.
**포터블 단일 모델 에이전트**로 고정되며
한 개의 llama.cpp 서버에 적재된 GGUF 모델 가중치를 공유한다.

1. main 역할: 답변 초안과 도구 호출
2. numeric 역할: 근거 기반 수치검증이 필요한 경우만 수행
3. final 역할: 사실/행위 검증 (품질검증 활성 시 같은 역할)
4. REJECT 시 main 체크포인트 복원 후 새 피드백을 반영한 다음 추론

`mai/llm/slot_cache.py`는 `/slots/0?action=save|restore|erase`를 호출한다.
`mai/llm/role_cache_runtime.py`는 역할을 전환하고,
`mai/app/runtime.py`는 한 사용자 턴이 종료될 때까지 해당 요청을 직렬화한다.

캐시 교체 기능은 llama.cpp의 **실제 슬롯 ID 0**을 대상으로 한다.
서버는 `--parallel 1 --slot-save-path <캐시 디렉터리> --jinja` 옵션으로 실행한다.
`/slots`에서 슬롯 수와 유휴 상태, `/v1/models`의 alias를 확인한다.
별도 외부 클라이언트가 같은 8080 포트로 요청하지 않도록 제한해야 한다.

KV 저장과 복원은 GPT 추론 상태의 일부를 재사용하는 최적화다.
원래 메시지 기록과 도구 결과는 별도로 보존하며, 캐시를 되돌린 시점 뒤
프롬프트의 접두사가 달라지는 경우 해당 부분은 다시 Prefill할 수 있다.
디스크 I/O와 메모리 이동 비용이 있어 반드시 빨라지는 것은 아니다.

모델은 한 벌만 적재한다. 숫자검증과 최종검증은 **같은 모델에 대해
독립된 JSON 구조화 호출**을 수행하며 별도 모델을 적재하지 않는다.
메모리 사실 추출도 같은 llama.cpp 서버로 실행한다.

`settings.yaml`의 `MODEL_GGUF_PATH`에서 실행할 GGUF 파일 경로 하나를 지정한다. llama.cpp 내부 alias는 `mai-model`로 고정된다. 시작기의 `-GgufPath`로 해당 실행에 한해 경로를 덮어쓸 수 있다.
브라우저에서 모델을 변경할 수 없고 다른 GGUF로 바꾸려면 서버를 재시작한다.
32K와 같은 임의의 컨텍스트 하한·상한을 별도로 두지 않는다.

테스트: `python -m pytest -q tests/test_slot_cache.py tests/test_role_cache_runtime.py tests/test_role_cache_app_wiring.py`.
코드 단위 테스트와 실제 GPU/Windows Vulkan 부하 테스트는 다르다.
이전 전원 차단 원인은 입증되지 않았으므로 안정성 확인 없이 12B의
GPU 풀로드를 강제하지 않는다.
