# MAI Remote Agent v1 — 임시 원격 작업 (WIP)

**상태: 개발 중. 현재 브랜치를 실제 타인 PC의 전체 관리에 사용하거나 공개 Funnel로 운용하지 말 것.**

## 사용 의도

MAI는 자신의 PC에서 계속 모델 추론과 기억 관리 및 검증을 담당함. 사용자가 다른 PC 앞에 직접 앉아 일회용 Agent를 실행하고 명시적으로 PC 전체 관리 권한을 위임하는 동안에만 원격 파일·명령 작업을 수행함. 종료 이후 상주 서비스·자동 시작·영구 기기 등록을 남기지 않음. 자취방/회사/친구 PC를 상시 등록해서 관리하는 기능이 아님.

원격 시스템 실행 권한은 **Agent를 실행한 OS 사용자 토큰**의 범위임. 관리자로 실행하면 그에 따른 강력한 권한이 부여되지만 OS의 권한 상승을 우회하지 않음. 회사 및 타인 소유 PC에서는 해당 기기의 소유자와 조직 정책상 허가가 필요함.

## v1 프로토콜 초안

- 사용자 로그인과 분리된 Owner 전용 `POST /remote/sessions`가 1시간짜리 임시 세션 ID와 비밀 키를 반환함.
- 현장 Agent가 HTTPS로 MAI 서버의 `POST /remote/agent/{session_id}/poll`에 연결해 요청을 받아 작업함. 외부 PC에서 인바운드 포트를 열지 않음.
- `POST /remote/agent/{session_id}/result`는 요청 ID별 실제 성공 또는 실패를 서버로 반환함.
- Owner는 `DELETE /remote/sessions/{session_id}`로 세션을 강제 회수할 수 있음.
- Agent의 정상 종료 시 `DELETE /remote/agent/{session_id}`를 호출하여 연결 권한을 회수하도록 구현함.

원격 도구는 `remote_file_read`, `remote_file_list`, `remote_file_write`, `remote_file_delete`, `remote_process_run`임. 도구가 호출할 때 `session_id`가 필요함. 기존 로컬 도구는 변경하지 않음. Agent는 전달된 셸 문자열을 직접 실행하지 않고 실행 파일 + 인수 배열로 프로세스를 실행함. 이 방식도 PC에 광범위한 영향을 미칠 수 있음.

## Windows 실행 파일 빌드 초안

**개발 PC에서** Python과 PyInstaller를 사용하여 단일 실행 파일을 만들 수 있음:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./build-remote-agent.ps1 -Python python
```

출력 예상 위치: `dist/mai-remote-agent.exe`. 원격 PC에는 Python/Ollama가 필요 없는 것을 목표로 함. 실제 다른 Windows PC에서 실행 가능한지 통합 검증은 아직 진행하지 않음.

## 사용 순서(현재는 API 개발 검증용)

1. MAI Server에서 Owner로 로그인한 뒤 Owner bearer token으로 임시 세션 API를 호출함. 세션 생성 응답의 `agent_secret`은 민감 정보이므로 공개 로그·채팅에 붙여넣지 않음.
2. 대상 PC에서 `mai-remote-agent.exe --server https://<본인의 MAI 도메인> --session <세션 ID>` 실행.
3. 대상 PC 화면에 표시된 원격 관리 안내를 읽고 `YES`를 입력한 뒤 임시 비밀 키를 입력함.
4. 원격 도구 호출은 사용자 소유 Owner 세션과 연결된 해당 Agent에서만 실행됨.
5. 사용이 끝나면 Agent 종료 후 Owner 화면에서 세션 회수 상태를 확인하고 실행 파일을 삭제할 수 있음.

## 필수 완료 조건

- 현재 종료 코드의 정상 종료 회수는 네트워크 단절·프로세스 강제 종료 시 보장되지 않음. 서버는 세션 만료와 일정 시간 동안 폴링 부재에 따라 새 작업을 거부하지만, **기존에 이미 시작된 장기 프로세스가 즉시 정지하는 것은 별개의 문제**임. 명시적인 heartbeat/lease 및 강제 종료의 종단간 검증 필요.
- Agent 인증 키가 유출되면 세션 유효 시간 동안 다른 프로세스에서 악용될 수 있음. 연결 1회 바인딩과 동시 접속 제한 및 고장 처리 검증 필요.
- 현재 로깅은 앱 로거 중심이며 log.sqlite3의 감사를 완료했다고 주장할 수 없음.
- 세션 생성과 상태 표시를 위한 Owner UI 미구현.
- 실행 결과 용량, 요청 중복, 실패 후 재실행의 원자성, 파일 변경 경합, 예외 상황 테스트 강화 필요.
- 실제 Windows 실행 파일 패키징 및 통합 실행 검증 필요.

위 조건을 충족하기 전까지 이 브랜치는 **WIP**, PR은 **병합 금지**임.
