# MAI Rust Behavior Test Specification (Contract v0.1)
> Rust core 진입 기준선; 원본: `python/tests/*`, `python/mai/*`, `python/settings.yaml`
> 사례 표기: **KEEP** 기능 유지, **REMOVE** 별도 검증 모델/MCP 전용 기능 폐기, **INVESTIGATE** 추가 테스트 필요. 주어진 조건은 Rust 구현 테스트를 위한 **규범적 기대 동작**이며 실제 E2E 통과를 뜻하지 않는다.
> 공통 fixture: FakeLlamaRouter, FakeClock, InMemoryJobStore, TempSqlite, MockRemotePoller, FakeToolRegistry, OfflineHTTP; 각 테스트는 네트워크·실데이터 비의존.

## 1. CI 테스트 계층
- U: Rust unit/property test, C: API/데이터 contract, I: 다중 컴포넌트 integration, E: Windows/Linux 실기 E2E.
- 공통 검증: 성공 결과뿐 아니라 **실패 이유와 작업 상태**, 취소 후 자원 정리, 사용자 분리, 라우팅 위치, 재시도/시간초과의 의미를 확인.
- 교차 테스트 fixture는 원본 Python 테스트에서 입력/출력/오류 문자열을 단계적으로 추출하여 `rust/tests/fixtures/`에 고정. 실제 fixture 파일은 후속 구현 턴에서 생성.

## 2. 우선순위 P0 — 첫 Rust Core의 필수 보존 계약
| ID | 계층 | Given → When → Then | 원본 참조 | 상태 |
| --- | --- | --- | --- | --- |
| CORE-01 | U/I | non-empty 메시지와 모델이 주어지면 요청-응답 상태 머신이 완료로 종료하고 최종 내용을 반환 | `test_agent_runtime.py` | KEEP |
| CORE-02 | U | 빈 메시지를 주면 모델 호출 없이 유효성 실패 | `mai/agent/runtime.py`, `app/runtime.py` | KEEP |
| CORE-03 | I | 도구 필요 응답 후 정상 툴 결과를 모델에 반영하고 다음 라운드 수행 | `test_native_lazy_tools.py` | KEEP |
| CORE-04 | I | 라운드 수 임의 상한 없이 유의미한 tool loop를 지속; 반복 실패/진행 없음 가드는 개별적으로 동작 | `test_agent_guards.py` | KEEP |
| CORE-05 | U/I | 최초 모델 호출에서 callable schema는 `tool_manual`뿐; 활성화 후 등록 schema 노출 | `test_native_lazy_tools.py` | KEEP |
| CORE-06 | U/I | 미활성/알 수 없는 도구 호출 시 handler 실행 없이 명확한 실패, 등록 상태 오염 없음 | `test_native_lazy_tools.py` | KEEP |
| CORE-07 | U | 잘못된 args를 추정 보정하지 않으며 validation error 반환 | `test_tool_registry.py` | KEEP |
| CORE-08 | U | async/sync handler 성공/예외/timeout을 구분; 무한 대기 및 success-shaped fallback 금지 | `test_tool_registry.py` | KEEP |
| CORE-09 | I | 필수 도구 활성화 후 실제 실행 없이 성공 선언하려 하면 추가 호출/실패 처리 | `test_native_lazy_tools.py` | KEEP |
| CORE-10 | U/I | 응답 스트림 중 취소 시 final을 저장하지 않고 상태 cancelled, child process/task 정리 | `test_chat_jobs.py`, `test_detached_cancel_prompt_contract.py` | KEEP |
| CORE-11 | I | 모델 timeout 때 partial reasoning은 감사/진단용, 검증된 최종 답변처럼 제공하지 않음 | `test_chat_jobs.py`, `test_llamacpp_stream_diagnostics.py` | KEEP |
| CORE-12 | I | 최종 완료/실패/취소 후 host 임시 artifact 및 작업별 remote scratch 정리 | `test_remote_tmp_cleanup.py`, `test_temporary_artifacts.py` | KEEP |
| CORE-13 | U | 로그 이벤트의 시각·라운드·상태와 사용자 응답을 분리; 잘못된 tool envelope를 정상으로 보지 않음 | `test_tool_results.py`, `test_remote_worker_services.py` | KEEP |
| CORE-14 | I | Verifier 호출·교대·재심사 단계 없음 | `mai/agent/verification.py` | REMOVE (검증기 관련 흐름) |

## 3. 모델/자원
| ID | 계층 | Given → When → Then | 원본 | 상태 |
| --- | --- | --- | --- | --- |
| MOD-01 | U/I | 모델 한 개 로드; 전환 시 이전 unload **확인 후** 다음 load | `test_llamacpp_confirmed_unload.py` | KEEP |
| MOD-02 | U | 상태 unknown/unload 실패일 때 다음 모델 로드·추론 금지 | 동일 | KEEP |
| MOD-03 | U | VRAM 추정치 없거나 보호 여유 미달이면 로드 거부 | `test_llamacpp_gpu_budget.py` | KEEP |
| MOD-04 | U | 가용 RAM 알 수 없거나 6144 MiB 미달이면 load 거부(설정 우선 적용) | 동일 | KEEP |
| MOD-05 | C | 컨텍스트 65536, 캐시 RAM 0, session history 16, Ornith Q8 기본 경로를 설정에서 반영 | `test_single_resident_portable.py` | KEEP |
| MOD-06 | I | 연속 tool rounds에서는 **동일 모델을 불필요하게 unload 하지 않음** | `test_llamacpp_confirmed_unload.py` | KEEP |
| MOD-07 | I | 모델 전환과 백그라운드 기억 추출이 race condition을 만들지 않음 | `mai/app/runtime.py` | KEEP |
| MOD-08 | I | `/models/select`와 list 응답은 실제 load 상태·모델 label을 반영 | `test_last_selected_model.py`, `test_gguf_diagnostics_labels.py` | KEEP |
| MOD-09 | C | 외부 OpenAI/Anthropic/OpenRouter 사용 가능 설정을 보존; auth/timeout 오류 분리 | `test_cloud_api.py` | KEEP |
| MOD-10 | I | 공용 8080 memory gateway와 MAI 자체 모델 lock의 충돌 없이 **하나의 모델 소유권/보호 정책** | `test_llamacpp_memory_gateway.py` | INVESTIGATE |

## 4. Host/Client·원격
| ID | 계층 | Given → When → Then | 원본 | 상태 |
| --- | --- | --- | --- | --- |
| REM-01 | U/I | 최초 Host; client switch 이후 2회 이상 tool 호출 모두 동일 client로 전달 | `test_host_client_workspace_routing.py` | KEEP |
| REM-02 | U | 이미 선택한 host/client 재선택시 명확한 no-op 메시지, 작업 대상 변화 없음 | `test_remote_worker_services.py` | KEEP |
| REM-03 | U/I | 연결 없는 client 선택 실패 시 Host 유지, client에서 연결 끊기면 **Host 자동 실행 금지** | `test_remote_worker_services.py` | KEEP |
| REM-04 | U | 외부 web/browser 도구는 Host-only, worker 직접 실행 불가 | 동일 | KEEP |
| REM-05 | I | client에서 native tool schema·name·입력·에러 타입 동등성 유지 | `test_remote_worker_services.py` | KEEP |
| REM-06 | U/I | 원격은 로컬 tool timeout ×2, 모델·전송 timeout은 각각 분리 | `test_remote_double_timeout.py` 등 | KEEP (정확한 기본값 재검증) |
| REM-07 | I | 10시간 유휴 연결 및 진행 중 원격 요청 만료 시 적절한 실패/취소·정리 | `test_remote_idle_timeout.py` | KEEP |
| REM-08 | U | 각 run은 `./tmp/<run_id>/` 소유, 경로 이탈·symlink 거부 | `test_remote_tmp_cleanup.py` | KEEP |
| REM-09 | I | cleanup(run A)는 run B 및 사용자 persistent 파일 삭제하지 않음, 반복 cleanup 안전 | 동일 | KEEP |
| REM-10 | I | 끝/실패/취소에도 cleanup 신호를 보내고, 연결 단절은 기록 후 재정리 가능하게 함 | 동일 | KEEP |
| REM-11 | I | 원격 실패 예외·envelope 손상은 정상 응답이 아닌 명시적 오류 | `test_remote_error_identity.py`, `test_remote_worker_services.py` | KEEP |

## 5. API/인증/대화
| ID | 계층 | Given → When → Then | 원본 | 상태 |
| --- | --- | --- | --- | --- |
| API-01 | C | `/login/session`으로 초기 세션 획득 후 `/login` 인증; 인증 누락/실패 접근 거부 | `test_auth_sessions.py`, `test_gaia_benchmark_harness.py` | KEEP |
| API-02 | C | 같은 user 재로그인 토큰 갱신, 다른 identity의 토큰은 취소하지 않음 | `test_auth_sessions.py` | KEEP |
| API-03 | C | owner/trial 도구·모델·DB 데이터 경계 유지 | `test_access_policy.py`, `test_trial_model_access.py` | KEEP |
| API-04 | C | job pending→running→completed/failed/cancelled 및 계정별 snapshot 접근 | `test_chat_jobs.py` | KEEP |
| API-05 | C | 완료 전에 tool/thinking/stream progress 조회 가능, 오류 payload도 보존 | 동일 | KEEP |
| API-06 | I | 새 요청이 같은 사용자 활성 작업을 교체시 적절히 cancel하고 다른 계정 작업은 영향 없음 | 동일 | KEEP |
| API-07 | C | 세션 역사·new chat·reset·제거를 사용자 DB별 분리 | `test_chat_sessions.py`, `test_reset_trial_chat.py` | KEEP |
| API-08 | C | 다운로드 secret bearer link 만료/외부 경로 및 업로드 이미지 미리보기 권한 | `test_download_links.py`, `test_upload_image_previews.py` | KEEP |
| API-09 | C | UI JS에서 사용 중인 모든 JSON field, HTTP status, stream event 순서 호환 | `mai/app/static/*.js`, `mai/app/server.py`, `resumable_chat.py` | INVESTIGATE |

## 6. 메모리·검색·Sentence_Breaker
| ID | 계층 | Given → When → Then | 원본 | 상태 |
| --- | --- | --- | --- | --- |
| MEM-01 | U/I | 사실/발화/anchor 저장과 자동·명시 기억 검색, 제한된 working graph | `test_memory_runtime.py` | KEEP |
| MEM-02 | U | Boolean query AND/OR/NOT, 인용·다중 어휘 및 잘못된 query 거부 | `test_native_memory_boolean_recall.py`, `mai/memory/recall/query.py` | KEEP |
| MEM-03 | I | 교차 사용자 검색·node_id 조회 유출 금지 | `test_native_memory_boolean_recall.py` | KEEP |
| MEM-04 | I | 이전 SQLite 데이터 마이그레이션, FTS 검색, 사실의 시간 선후관계 보존 | `test_memory_repository_migration.py`, `test_sqlite_fts_index.py` | KEEP |
| MEM-05 | I | 쓰기 후 재검색 반영, 실패시 rollback, 잘못된 기억 승인 상태 방지 | `test_memory_read_after_write.py`, `test_memory_admission.py` | KEEP |
| SEG-01 | U | 공백 입력 거부, 빈 세그먼트 배제, split 순서 보존 | `mai/memory/segmenter.py` | KEEP |
| SEG-02 | U/P | 원본 LanguageGraph corpus에 대해 Rust segmentation 동등성 검증 | 외부 Sentence_Breaker 원본 테스트 필요 | INVESTIGATE |
| SEG-03 | I/P | 원본 Sentence_Breaker DB와 Rust 신규 DB 사용시 백업/동등성/호환성 | `.env.example` | INVESTIGATE |

## 7. 음성·UI
| ID | 계층 | Given → When → Then | 원본 | 상태 |
| --- | --- | --- | --- | --- |
| UI-01 | C/E | 기존 `index.html` 및 JS 수정 없이 로그인/모델 선택/채팅/작업중지 | `test_icon_first_web_ui.py`, `test_voice_input_ui.cjs` | KEEP |
| UI-02 | C/E | 짧은 음성 전사는 처리; 비어있거나 무음이면 422/insufficient_speech | `test_short_voice_input.py` | KEEP |
| UI-03 | C | TTS 실패가 STT를 중단하지 않음; 브라우저 지원 음성 목록 및 선택/상태 | `test_voice_*.`, `test_voice_web_speech_ui.cjs` | KEEP |
| UI-04 | C/E | 음성 입력 중 활성 발화로 유휴 카운트 리셋; 모델 스트리밍 중 취소 보장 | `test_voice_continuous_listening_ui.cjs` | KEEP |
| UI-05 | C | 이미지/vision projector 보유 모델의 유효 입력과 비지원 모델 실패 구분 | `test_main_model_image_input.py` | KEEP |

## 8. 벤치마크·배포
| ID | 계층 | Given → When → Then | 원본 | 상태 |
| --- | --- | --- | --- | --- |
| BEN-01 | U | GAIA 문제 입력에 private answer 포함 금지, 최종 답변 추출 계약 | `test_gaia_benchmark_harness.py` | KEEP |
| BEN-02 | U | dataset 밖 첨부 경로 거부 및 resume 중복 ID 거부 | 동일 | KEEP |
| BEN-03 | I | job poll 실패 결과 보존, pre-auth login 필수, 여러 owner 지정 필요 | 동일 | KEEP |
| BEN-04 | U | LongMemEval 세션 원문 그대로 replay, 홀수 턴·정렬 오류·잘못된 slicing 거부 | `test_longmemeval_benchmark.py` | KEEP |
| BEN-05 | U | 데이터 다운로드 손상시 SHA 검증·atomic publish 실패 처리 | 동일 | KEEP |
| BEN-06 | I | OpenClaw 비교 adapter의 입력·채점·결과 manifest 호환 | `test_gaia_openclaw_adapter.py`, `test_longmemeval_openclaw_adapter.py` | KEEP (Python/JS 외부 의존 평가) |
| DEP-01 | I | Windows/Linux 실행·프로세스 정리·signed artifact/version/hash 검증 | `test_windows_signed_portable_python_contract.py`, `test_linux_llamacpp_portable.py` | KEEP (Rust 배포로 의미 변환) |
| DEP-02 | I | 재시작/handoff·이전 데이터 경로·host provider 상태 반영 | `test_restart_lifecycle.py`, `test_runtime_data_path_migration.py` | KEEP |

## 9. 명시적 제거 목록 (Rust 테스트 요구 아님)
- `test_verifier_*.py`, `test_final_grounding_verifier.py`, `test_prompt_cache_timestamp_verifier.py` 등의 verifier-specific 승인/거부/모델 교대 검증.
- `mcp_input_schema` 경유 JSON Schema 분기·MCP 레지스트리/전송 프로세스·`mcp_memorizer` 테스트. **동일 도구의 사용자-facing 기능은 Native Tool 테스트로 대체**한다.
- UI `verify` 토글 및 verifier 전용 progress 이벤트는 UI 수정 없이 호환성 처리가 가능한지 우선 확인하고, 향후 UI 정리 여부는 별도 승인을 받는다.

## 10. Rust Core 최소 구현 진입 게이트
- 초기 공통 메시지 schema, `AgentState`, `ToolDefinition`, `ToolCall`, `RunId`, `ExecutionLocation`, `CancellationToken`, `Event` 정의.
- Fake adapter로 **CORE-01~13 중 실행 가능한 단위 테스트** 작성; 진짜 모델 호출이 없어도 재현 가능해야 한다.
- `model_stream`/도구 출력/취소의 순서·최종 이벤트 단일성을 테스트; 모델 라우터와 Web UI 연결은 후속 단계.
- 이 명세의 아직 추정인 항목은 실행 코드와 실제 golden fixtures가 확인되기 전까지 Rust 구현의 '기존과 완전 동일' 보증으로 사용하지 않는다.
