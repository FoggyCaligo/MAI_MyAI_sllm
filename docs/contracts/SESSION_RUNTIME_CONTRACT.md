# Mai Session / Authorization Runtime Contract

이 문서는 MK4에서 검증된 session, request-detached chat, account tool scope, file working-root 개념을 현재 Mai 계약에 맞게 정의한다.

## 1. Persistent authenticated session

- 로그인 token 원문은 SQLite에 저장하지 않는다.
- cookie에는 random bearer token을 두고 DB에는 SHA-256 hash만 저장한다.
- session은 stable `session_id`, `user_id`, `role`, `working_root`, `expires_at`을 가진다.
- 기본 TTL은 `MAI_SESSION_TTL_SECONDS`로 설정하며 기본값은 30일이다.
- 서버 프로세스가 재시작되어도 아직 TTL이 유효한 session은 유지된다.
- logout은 DB session row를 제거하고 cookie를 삭제한다.
- 요청마다 현재 allowed-user 설정과 owner/trial role을 다시 구조적으로 검증한다. 저장된 role이 현재 설정과 맞지 않으면 session을 계속 신뢰하지 않는다.

### Trial single-session contract

- `trial` user ID 하나에는 활성 persistent session을 하나만 허용한다.
- 같은 trial user ID로 새 login session을 생성하면 기존 동일-user trial session row를 같은 session-store lock/transaction 안에서 먼저 제거한다.
- 이전 browser/device의 bearer token은 다음 요청부터 더 이상 session으로 해석되지 않는다.
- 이전 trial session에 귀속된 queued/running chat job은 실제 lifecycle 실행 직전에 stable `session_id`를 다시 조회하므로, session이 교체됐다면 명확한 authorization failure로 실패한다.
- `owner`는 이 single-session 제한을 적용하지 않는다.
- 다른 trial user ID의 session은 서로 폐기하지 않는다.

이 규칙은 IP, User-Agent, user text 등의 heuristic으로 “같은 사람”을 추정하지 않는다. Authenticated `user_id + role`이라는 구조적 session identity만 사용한다.

## 2. Account roles and tool exposure

역할은 Framework가 authenticated user identity에서 구조적으로 결정한다.

- `owner`: 전체 등록 work tool catalog
- `trial`: public web/market work-tool suite만

`node_lookup`, `recall_memory`, final semantic memory mutation은 work-tool catalog와 별개의 core agent capability이므로 양쪽 역할에서 유지한다.

Trial restriction은 user text를 해석해서 route하지 않는다. Owner-only file/terminal/code/document/image tool은 trial의 compact catalog, `tool_manual` target, executable schema에 애초에 존재하지 않는다. Upload/download host-file endpoints도 owner-only다.

## 3. Request-detached chat jobs

`POST /chat`은 lifecycle 완료까지 HTTP request를 붙잡지 않는다.

```text
POST /chat
  -> persistent chat_jobs row(status=pending)
  -> detached worker starts
  -> immediate {job_id, status}

GET /chat/jobs/{job_id}
  -> pending | running | completed | failed | interrupted
```

Job은 authenticated `user_id`와 stable `session_id`에 귀속되며 다른 user의 job ID를 읽을 수 없다.

동일 user의 lifecycle jobs는 user lock으로 직렬화한다. 서로 다른 user는 독립적으로 실행될 수 있다.

Queued job은 HTTP 요청 시점의 `SessionRecord`를 계속 신뢰하지 않는다. 실제 user lock을 얻은 시점에 stable `session_id`로 persistent session을 다시 읽는다. 따라서 앞선 job이 working root를 바꿨다면 다음 job은 최신 root를 사용한다. Session이 그 사이 expire/revoke되거나 현재 account policy와 달라졌으면 job은 명확히 실패한다.

완료된 job만 raw chat history와 compact tool-operation history에 성공 turn으로 기록한다.

실패한 lifecycle은 `failed`로 남고 fake assistant history를 만들지 않는다.

서버가 재시작되면 이전 프로세스의 `pending/running` job을 성공으로 추측하거나 재실행하지 않는다. DB에서 `interrupted` + `server_restarted_during_execution`으로 명시한다.

## 4. UI reconnect behavior

UI는 `POST /chat`에서 받은 `job_id`를 poll한다.

페이지에 머물러 있지 않더라도 서버 job은 HTTP request와 독립적으로 계속 실행된다.

UI가 다시 load되면:
- `/history`에서 확정 완료된 turn을 복원한다.
- `/chat/jobs`에서 현재 `pending/running` job을 복원한다.
- active job에는 기존 Mai three-dot thinking loader를 다시 표시하고 동일 job ID를 poll한다.

## 5. Session file/code working root

Working root는 owner의 file/code discovery 편의 기준점이지 sandbox/security boundary가 아니다.

- session 생성 시 `Path.cwd()`를 initial root로 저장한다.
- owner lifecycle의 file discovery와 code discovery tool은 이 root를 default root로 사용한다.
- 현재 working root는 짧은 structured runtime context로 모델에게 전달한다.
- 모델이 일반 텍스트만으로 working root를 선언할 수 없다.
- working root를 승격할 수 있는 tool은 `WorkingRootToolAdapter`로 성공 result의 root field를 명시적으로 선언한다.
- Agent는 선언된 tool의 성공 event에만 top-level `working_root` metadata를 붙인다.
- session 계층은 tool name이나 임의 result field를 해석하지 않고 해당 metadata만 반영한다.
- 후보 root는 실제 존재하는 directory여야 한다.
- unrelated tool result에 우연히 `root` 같은 필드가 있어도 working root를 변경하지 않는다.
- 같은 user의 queued job은 실행 직전 최신 persistent session을 다시 읽으므로 앞선 job이 승격한 root를 이어받는다.

Working root는 기존 current-turn path provenance를 대체하지 않는다. Existing-file mutation은 여전히 현재 turn에 실제로 established된 concrete path만 사용한다. Owner의 절대경로/상위경로 접근도 인위적으로 금지하지 않으며 실제 OS/filesystem permission이 최종 경계다.

## 6. Failure visibility

다음은 자동 성공 처리하지 않는다.

- expired/unknown session -> authentication failure
- 현재 allowed-user/role 설정과 맞지 않는 persisted session -> authorization failure
- replaced trial session -> authentication/authorization failure on the old session
- queued job 실행 전에 session revoke/expire/replace -> failed job
- foreign job -> not found in caller scope
- lifecycle exception -> failed job with concrete exception type/message
- process restart during job -> interrupted
- invalid working-root metadata -> explicit path error
- missing session row on working-root update -> explicit error

Request-detached execution은 실패를 숨기는 fallback이 아니다. 실행 결과를 HTTP request 수명과 분리하여 persistent state로 보존하는 계층이다.
