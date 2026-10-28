# 로그인 IP별 10회 실패 잠금 (검증 대기)

## 요청된 정책

- 로그인 실패를 출발지 IP 단위로 누적.
- 동일 IP에서 연속 실패 10회가 되면 HTTP 429 및 Retry-After 3600초.
- 잠금 시간 1시간 동안 같은 IP의 로그인 거부.
- 다른 IP의 계정 로그인은 영향 없어야 함.
- 정상 로그인 시 그 IP에 대해서만 실패 횟수를 초기화.
- log.sqlite3에 보존하여 MAI 서버 재시작에도 유지.
- Owner 로그아웃 시 같은 Owner가 발급한 임시 Remote Agent 세션 인증 키도 폐기.

## 중요한 미해결 조건: Tailscale Funnel

현재 FastAPI/Uvicorn은 Request.client.host를 기준으로 IP별 잠금을 계산함. 그러나 Tailscale Funnel은 프록시이므로 Request.client.host가 실제 로그인 시도자의 IP가 아닐 수 있음. 특히 여러 사용자가 동일한 프록시 출발지 주소로 표시되면 공격자 10회 실패가 Owner의 로그인까지 잠그는 심각한 오작동 발생.

**따라서 현재 구현을 TAILSCALE_FUNNEL=true 환경에 병합·배포하면 안 됨.** Tailscale Funnel의 PROXY protocol v2와 이 프로토콜을 검증하여 받아들이는 전용 로컬 리버스 프록시 등, 신뢰할 수 있는 원본 IP 전파 경로를 먼저 설계하고 실기기 테스트해야 함. 신뢰하지 않은 X-Forwarded-For 등을 직접 읽어 IP를 판단하지 말 것. 정상 접속한 사용자에게도 공유 프록시 IP의 잠금이 전파되지 않음을 검증해야 함.

본 변경은 서버 API 동작과 단위 테스트 초안까지 작성한 **WIP**이며, Windows/Tailscale Funnel 종단간 테스트 미실시.
