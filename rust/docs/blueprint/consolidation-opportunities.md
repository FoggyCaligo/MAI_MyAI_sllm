# MAI 인터페이스·계층 통합 후보 보고서
> 대상: `python/` 정적 감사 + 선택 테스트 assertion. **기능 통합**이지 보안·프로세스 경계 제거가 아니다.
> 판정: A = 바로 설계 반영, B = 검증 후 통합, C = 경계 보존. 실제 성능 개선량은 미측정.

## 1. 우선순위별 통합 후보
| ID/우선 | 근거(원본 경로) | 관찰한 기워진 구조 | 제안 | 판정·주의 |
| --- | --- | --- | --- | --- |
| C01 / P0 | `mai/app/server.py`, `resumable_chat.py`, `chat_jobs.py`, `mai/app/runtime.py`, `mai/agent/runtime.py` | 요청 처리, 작업 관리, 실행, 이벤트 publish callback이 여러 층을 경유. sync `/chat`와 resumable job 흐름도 별도 | Rust `ChatService` 하나가 job 상태+event stream을 소유. `AgentRuntime`은 단일 typed event channel로 보고. 동기 API는 job을 동기적으로 기다리는 얇은 호환 라우트 | A. UI JSON/SSE/HTTP 응답 계약은 보존 |
| C02 / P0 | `mai/tools/registry.py`, `remote/routing.py`, `remote/python_worker.py` | Host용 ToolRegistry를 만든 뒤 정의를 복사해 Client 라우팅 wrapper로 재등록, remote worker에서 또 별도 registry 생성 | **ToolDescriptor 단일 선언 + Executor(Host/Client)**. 동일 schema를 serialize해서 worker에도 노출, transport routing은 단일 dispatcher. 도구 category를 실행 위치 정책과 혼동하지 않음 | A. worker 허용목록/Host-only web/browser와 owner 인증은 유지 |
| C03 / P0 | `mai/tools/registry.py`의 `metadata["mcp_input_schema"]`, `mai/tools/tool_manual.py` | 네이티브 schema와 MCP JSON schema를 조건부 분기, 도구 활성화 schema도 복사/수정 | Rust typed input schema에서 model callable schema와 validation을 같은 정의로 생성; 별도 MCP schema/compat path 제거 | A. `tool_manual` lazy disclosure 유지 |
| C04 / P0 | `mai/agent/loop.py`, `agent/runtime.py`, `app/runtime.py`, `app/resumable_chat.py` | Verifier용 콜백·재시도·추가 adapter/prompt/response/event 체인 | verification 전용 코드·UI 이벤트·config 정리. 기본 실패·근거 로깅은 main agent 독립 기능으로 유지 | A. 'verifier 비활성화'가 아니라 완전 제거 |
| C05 / P1 | `mai/llm/llamacpp_router.py`, `portable/llamacpp_memory_gateway.py`, `mai/app/runtime.py` | Router 로딩 lock·model scope·gateway upstream lock 등 **동일 자원 보호와 생명주기가 복수 지점에 분산** | Rust `ModelSupervisor` 1개가 1-resident transaction과 RAM/VRAM gate를 소유하고 모든 클라이언트가 동일 supervisor policy를 거치도록 통합 | **B**. 게이트웨이는 외부 OpenClaw 등 공유 클라이언트를 보호하므로 함부로 삭제 금지. 네트워크 단일 진입점/락 스코프 실측 필요 |
| C06 / P1 | `mai/memory/{runtime,recall/service,recall/query,index/sqlite_fts,graph/repository,working}` | graph/index/working/intermediate service에 반복 데이터 전달 | Rust `MemoryStore`(SQLite transaction + FTS)·`RecallEngine`·`WorkingMemory` 3개 책임으로 단순화 검토 | B. DB 기록 일관성/검색 품질/유저 분리 필요 |
| C07 / P1 | `mai/memory/segmenter.py`, `sentence_breaker.LanguageGraph` | MAI→Protocol→Adapter→외부 패키지→DB | Rust `sentence-breaker` crate 직접 호출, 기존 LanguageGraph 의미와 결과만 보존 | A. 실 알고리즘 소스 미확보, 구현은 보류 |
| C08 / P1 | `remote/session.py`, `remote/agent_client.py`, `remote/workspace.py`, `remote/target.py` | session/target/workspace/client의 다중 위임 | Rust `RemoteSessionManager` + `ExecutionLocation` + `WorkerTransport` 통합형 명세. 영속 location, owner binding, run_id를 공통 `RunContext`로 취급 | B. connection loss·revocation·유휴 10h 보호는 유지 |
| C09 / P1 | `mai/tools/artifacts.py`, `remote/temp_workspace.py`, `remote/workspace.py`, `agent/runtime.py` | host 임시 artifact scope와 worker cleanup 메시지의 별도 수명주기 관리 | Rust `RunWorkspaceGuard` 단일 소유 객체가 host/remote scratch를 run_id로 관리하고 finally/Drop + async cleanup 프로토콜로 정리 | A. worker 파일은 원격에서 직접 삭제해야 함. 비동기 Drop의 한계로 보상 작업 큐 필요 |
| C10 / P2 | `mai/app/access.py`, `app/chat_sessions.py`, `memory/graph/repository.py`, `app/chat_jobs.py` | auth user/db_id/memory user_id가 서브시스템마다 전달 | Rust `PrincipalContext`에 권한·DB scope·memory scope를 typed 값으로 묶어 전달 | A. 값들을 동일한 ID 하나로 합쳐서 데이터 격리를 깨지 말 것 |
| C11 / P2 | `mai/llm/llamacpp.py`, `cloud_api.py`, `models.py` | 공급자별 호출/응답 및 오류/stream mapping | Rust `ModelClient` trait 하나와 llama.cpp, OpenAI-compatible, Anthropic 구현; 공통 요청/응답으로 정규화 | B. provider별 실제 응답/모달리티/토큰 제한 다름. provider를 하나로 강제하지 않음 |
| C12 / P2 | `portable/*`, `mai.sh`, `mai.ps1`, `MAI_light_instant_runner/*` | Windows PowerShell/Bash/Python/C# 여러 런처와 설치 도구 | Rust `mai-cli`에서 check/setup/update/start/restart를 단일 인터페이스로 제공하고 플랫폼별 외부 바이너리 어댑터만 유지 | B. Windows 서명 검증·Linux GPU backend·업데이트 rollback 필수 |

## 2. 제거해서는 안 되는 경계 (통합과 제거의 차이)
- [C] **llama.cpp 서버 프로세스 격리**: native Rust에 C++ 추론 엔진 전체를 통합하는 것은 불필요. out-of-process 장애 격리 보존.
- [C] **Host vs Client 실행 위치/권한**: 실행기는 공통화하되 파일 접근이 원격으로 향하는 사실, web/browser Host-only, client no-fallback은 불변.
- [C] **사용자 데이터/DB identity 구분**: owner/trial, login token, chat DB, memory graph의 별도 보안 경계.
- [C] **공식 벤치마크 점수 계산 기준**: Rust 실행기로 통합해도 GAIA/LongMemEval 원문·공식 채점기·외부 OpenClaw adapter 요구는 훼손 금지.
- [C] **Web UI 파일 및 브라우저 기능**: UI 재작성이 아니므로 JS/API 계약은 adapter로 맞춘다.
- [C] **로컬 임시파일과 원격 임시파일의 물리적 위치**: 한 lifecycle은 가능하지만 실제 파일 소유 주체는 서로 다름.

## 3. 실제로 발견된 설계 불일치·주의
1. `python/settings.yaml`은 Q8 GGUF와 model timeout 500초를 명시; 이전 논의의 버전들과 섞지 말 것. **Rust는 settings.yaml을 단일 우선순위 규칙에 따라 읽도록 한다**.
2. `mai/memory/segmenter.py`는 별도 LanguageGraph 자체를 구현하지 않으며 빈 문자열 검사·필터만 제공한다. '그대로 포팅'으로는 Sentence_Breaker 이식 불가.
3. `mai/tools/registry.py`는 `mcp_input_schema` 분기가 남아 있으나, `mai/app/runtime.py`의 복구 branch 주 경로는 직접 Native ToolRegistry로 마운트한다. 폐기는 잔여 호환 분기와 테스트에 국한하여 추적.
4. `MAI_VERIFIER_ENABLED=false`라고 해서 verifier 코드가 죽은 코드가 되지 않는다. 요청별 `verify` 필드, agent/runtime, loop, rejection progress, 장치/문서/테스트 모두 경로가 살아있음.
5. `LLAMACPP_MEMORY_GUARD_ENABLED=true`이면 공유 gateway가 다른 클라이언트 호출도 보호. MAI 내부 라우터만 남기면 안전 정책이 **축소**될 수 있다.
6. 기존 memory recall 테스트 중에는 분할 없이 공백 단위 질의를 사용하는 경로도 있다. Sentence_Breaker를 무조건 모든 검색에 적용하면 검색 결과 달라질 수 있음.

## 4. 실행 우선순위와 기대 효과
- **첫 구현**: C01+C02+C03+C04 → core 상태/event 단일화, 도구 선언·routing 단일화, MCP/Verifier 제거. 복잡도 감소가 확실하나 성능 %는 미측정.
- **이후**: C09+C08+C07 → 작업 소유권·원격/임시수명 통합, Sentence_Breaker 직접 Rust 이식.
- **측정 후**: C05+C06+C11+C12 → 공유 모델 게이트, memory DB, provider, 설치 런처. 실제 lock contention/latency 및 호환성 비교 후 결정.
- 불필요한 인터페이스 제거의 성공 기준은 **public behavior parity, 더 적은 state owner/dispatcher/adapter, 실패 분기 감소, 코드/프로세스 수 감소(측정)**이며 무조건 파일/모듈 숫자가 작은 것이 아님.
