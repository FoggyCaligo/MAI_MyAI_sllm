# KV 캐시 교체 실험 — dev 기준 독립 브랜치

이 브랜치는 **현재 dev**에서 만들었다. 기존 dev 및 dev2 코드를 변경하지 않는다.

## 목표

하나의 동일한 llama.cpp 모델 가중치를 GPU에 유지하면서 세 역할의
프롬프트/KV 캐시를 순차 교체한다.

1. main: 작업, 도구 호출, 임시답변 생성
2. numeric: 숫자 검증
3. final: 전체 사실 검증
4. REJECT가 나면 main 체크포인트를 복원한 다음 최신 교정 피드백 처리

복원 후 이어지는 요청이 저장 당시의 프롬프트 토큰 Prefix와 동일해야
실제 Prefill 재사용 이점이 있다. 저장 당시의 KV 상태에서 임의의
새 프롬프트를 이어붙이는 것은 안전하지 않다.

## 현재 구현 수준

- `mai/llm/slot_cache.py`: llama.cpp 슬롯 save/restore/erase용 HTTP
  클라이언트, main/numeric/final 별 체크포인트, 역할 전환 상태기계
- `tests/test_slot_cache.py`: 가짜 llama.cpp 서버를 이용한 성공 및
  실패 시나리오
- 기본 MAI AgentLoop에는 **연결되지 않음**.
  지금은 `LLM_BACKEND=ollama`의 동작이 바뀌지 않는다.

## 실험을 켜기 전에 필요한 것

- llama.cpp 버전에서 슬롯 API 지원 확인
- 별도의 하나의 슬롯과 모델 고정 (`--parallel 1` 등)
- `--slot-save-path`를 안전한 전용 디렉터리로 설정
- 서버에 대한 다른 요청 차단 및 모델/토크나이저/채팅 템플릿 불변 조건 확인
- 저장 및 복원 API 호출의 타이밍: **추론 요청이 완전히 종료된 이후**
- 각 요청을 정확히 같은 슬롯으로 보내고 다른 요청의 끼어들기를 막는
  서버 측 슬롯 affinity 검증
- 캐시 파일의 계정/세션 격리, 작업 종료 후 삭제 및 장애 복구
- llama.cpp와 Ollama 간 자동 백엔드 전환 없음

현재 `LlamaCppAdapter`는 OpenAI 호환 `/v1/chat/completions`을 사용하며
슬롯 ID 고정 및 요청 간 독점 예약을 보장하지 않는다. 이 조건을
구현·검증하기 전에는 실제 AgentLoop에 캐시 저장/복원을 연결하면
다른 요청의 캐시를 덮을 수 있다.

따라서 본 브랜치의 코드는 **호출 가능한 격리 실험용 구성요소**이며,
실제 MAI 응답 시간/VRAM 개선이 입증된 기능으로 간주하지 않는다.

## 검증

```bash
python -m pytest -q tests/test_slot_cache.py
python -m pytest -q
```

실측 시에는 다음을 함께 기록한다: CPU/GPU 메모리 사용량, 슬롯
save_ms/restore_ms, Prefill 토큰 수, 전체 ACCEPT 소요시간,
REJECT 후 복귀시간 및 실패율.

## 안전

슬롯 캐시 저장 파일에는 사용자의 입력에서 파생된 민감한 표현이
포함될 수 있으므로 외부 공유·Git 커밋·무기한 보관을 금지한다.
