# llama.cpp 타임아웃 추론 진단

## 추론 타임아웃 부분 출력 진단

MAI 웹 채팅의 Main 모델 요청은 llama.cpp의 SSE 스트리밍을 소비하되, **완전한 종료 이벤트를 받은 뒤에만** 기존의 ModelTurn 계약으로 전달한다. 이는 UI의 실시간 스트리밍 답변 기능이 아니며, 도구 호출·임시답변·최종답변을 부분 출력에서 실행하거나 확정하지 않는다.

타임아웃이 발생하면 그 시점까지 수신된 `reasoning_content`와 `content`를 감사 SQLite `steps`의 기존 `model_think_step` 및 `model` 역할로 보존하고, `status=timeout_partial`과 `details_json.incomplete=true`를 명시한다. 도구 호출 중간 조각은 실행하지 않고 `details_json.tool_fragments`에 기록한다. `round_number` 및 `error_type`이 함께 기록되어 기존 감사 실행의 실패 원인과 연결할 수 있다.

단순히 채팅 API 요청을 스트리밍으로 바꾼 것은 아니며, 감사 콜백이 없는 내부 모델 호출은 기존 `stream=false` 응답 경로를 유지한다. 현재 기본값은 `settings.yaml`의 `MODEL_REQUEST_TIMEOUT_SECONDS: 300`이며 **각 llama.cpp 모델 호출마다** 적용된다. GAIA 실행기의 문제별 1,800초 제한이나 사용자 요청 전체 타임아웃과는 별개다. 타임아웃 제한은 기존 `MODEL_REQUEST_TIMEOUT_SECONDS` 그대로이고, 스트리밍에서는 토큰이 계속 생성되어도 전체 요청 시간이 이를 초과하면 종료된다.

추론 내용은 개인 문맥·원격 경로·명령 등 민감정보를 포함할 수 있으므로 감사 DB의 접근 권한을 엄격히 관리해야 한다. llama.cpp가 특정 채널을 `reasoning_content`로 분리하지 않았다면 기록도 이를 추정해서 구분하지 않는다. 마찬가지로 최종 SSE 종료를 받지 못한 정상 소켓 종료는 프로토콜 오류로 처리되며 부분 완료로 간주하지 않는다.
