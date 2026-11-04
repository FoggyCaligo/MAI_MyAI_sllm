# 브라우저 로그인 세션별 실패 제한

- MAI 로그인 화면에서는 로그인 시도 전에 `GET /login/session`의 JSON에서 `session_token`을 발급받음. 브라우저는 `sessionStorage`에 보관하고 로그인 요청의 `X-MAI-Preauth-Session` 헤더로 전송함.
- 동일한 브라우저 세션에서 비밀번호 인증 실패가 10회 누적되면 1시간 잠금. HTTP 429와 `Retry-After` 헤더 반환.
- 정상 로그인 성공 시 해당 세션의 실패 횟수만 초기화.
- 다른 브라우저 세션과 IP에는 영향이 없음. Tailscale Funnel 프록시의 공용 IP를 기준으로 차단하지 않음.
- 로그인 실패 기록은 `log.sqlite3`에 세션 토큰 원문이 아닌 SHA-256 해시로 저장하며 서버 재시작 시에도 유지.
- 이 사전 로그인 경로는 쿠키를 사용하지 않으며 24시간 쿠키 만료도 없음. token 형식 검사와 실패 잠금 시간은 서버에서 처리함. HTTP localhost와 HTTPS 프록시 모두에서 같은 헤더 계약을 사용함.
- sessionStorage 제거/새 브라우저 세션 생성으로 횟수를 우회할 수 있음. 공격자가 계속 세션을 재발급하면 이 제한만으로는 비밀번호 추측 공격을 막지 못함.
- 사용자가 MAI 웹에서 로그아웃하면 해당 브라우저 인증 토큰만 폐기하며, 이미 연결된 Owner 원격 관리 세션은 유지되어 재로그인 시 `/remote/sessions/current`로 다시 조회할 수 있음(원격 세션 종료는 **연결 해제** 또는 Agent 종료·만료 시 수행).

## 테스트

동일 세션 9회 실패는 허용, 10회째는 3600초 잠금, 다른 세션은 영향 없음, 1시간 경과 시 다시 시도, 로그인 성공 시 실패 횟수 초기화를 단위 테스트로 확인해야 함.

구현 근거: `mai/app/server.py`의 `browser_login_session`·`login`, `mai/app/login_rate_limit.py`, `mai/app/static/index.html`. 테스트는 `tests/test_login_ip_lockout.py`와 `tests/test_login_password_contract.py`를 참고함. 실제 Windows Funnel 종단간 동작은 별도 환경에서 확인해야 함.
