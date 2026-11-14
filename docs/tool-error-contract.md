# 도구 실패 반환 계약

`AgentLoop`는 네이티브와 기능별 MCP 도구 호출 실패를 동일한 **모델 가시 JSON 형태**로 반환한다. 성공 결과의 원래 구조는 변경하지 않는다.

```json
{
  "ok": false,
  "error_type": "FileNotFoundError",
  "error_category": "not_found",
  "retryable": false,
  "message": "missing.txt"
}
```

- `error_type`: 실제 예외 클래스 이름. 기존 소비자·감사 기록과의 호환을 위해 유지.
- `error_category`: 도구 유형과 무관하게 안정적으로 분류하는 코드.
- `retryable`: 같은 실패를 영구 실패로 재사용하지 않을지에 대한 힌트. `true`라도 반복 요청이 성공한다는 보장은 없음.
- `message`: 원래 예외 설명(터미널 예외에는 종료 코드, stdout/stderr 등의 구조화 데이터가 문자열로 포함됨).
- `required_activation`·`unknown_tools` 같은 제어 도구 전용 필드는 필요한 경우 추가된다.

| 분류 | 예시 | 기본 재시도 |
| --- | --- | --- |
| `invalid_arguments`, `unknown_tool`, `not_activated` | 잘못된 스키마·미등록·미활성화 | 아니오 |
| `timeout`, `connection_error`, `remote_execution_error` | 시간 초과·연결 오류·워커 오류 | 예 |
| `permission_denied`, `not_found`, `conflict` | 권한·경로·기존 파일 충돌 | 아니오 |
| `encoding_error`, `output_limit`, `command_failed` | 문자 디코딩·출력 제한·비영 종료 코드 | 아니오 |
| `execution_failed` | 그 밖의 실행 오류·MCP 서버 도구 오류 | 예 (불명확하므로 영구 캐시하지 않음) |

**보안/정합성:** 외부 MCP 서버의 `isError=true`는 실행 실패이지 연결 실패가 아니다. 실패를 `ok=true`로 변환하거나 `stderr` 여부만으로 실행 실패를 판단하지 않는다. 일부 동기 핸들러의 `asyncio.wait_for`는 호출 대기를 중단해도 백그라운드 스레드 자체를 강제 중단하지 못하므로, 파일 쓰기 등 부작용 작업의 재시도는 신중해야 한다.

[실행 코드](../mai/agent/tool_errors.py) · [도구 등록소](../mai/tools/registry.py) · [터미널](../mai/tools/terminal.py).
