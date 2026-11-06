# Verifier 입력 데이터 계약 (dev 전환안)

기본 사실검증은 Ollama `think=false`, `tools=()`, 검증 결과 JSON Schema를 사용한다. 시스템 프롬프트는 `mai/agent/verification.py`의 `_FACTUAL_REVIEW_SYSTEM`; 숫자 동등성 의문이 있으면 같은 요청에 `_NUMERIC_REVIEW_SYSTEM`을 추가한다. 선택적 답변 품질검증은 `MAI_ANSWER_QUALITY_REVIEW=true`에서만 별도 요청으로 진행한다.

## 검증 입력 JSON

- `datetime_local_iso`: 런타임이 제공하는 UTC 오프셋 포함 현지 ISO 8601 현재시각. 과거의 `authoritative_current_time` 객체를 대체.
- `current_user_request`: 가장 최근 사용자 요청의 잘림 제한 표시를 포함한 복사본.
- `conversation_context`: 이전 사용자/어시스턴트 발화를 실행 순서대로 한 번씩 저장. 각 항목은 `role`, `content`, `verified_prior`를 가진다. 이전 검증 통과 대화임이 세션 기록에서 확인된 경우에만 `verified_prior=true`. 사용자 발화는 증거이나 미검증 어시스턴트 발화는 문맥만 제공.
- `tool_results_in_execution_order`: 각각 `index`, `tool`, `params`, `endtype`, `result`를 가진다. 성공은 `endtype="success"`, 실패는 `endtype="error_type:<TypeName>"`임. 구버전 기록처럼 호출 인자가 미수집된 경우 `params=null`로 명시한다. 실시간 실행은 원본 호출 인자 JSON 객체를 전달한다. `web_search` 성공 관측에는 출처 메타 설명 `evidence_note`가 추가될 수 있다. `tool_manual`의 활성화 결과는 제외한다.
- `candidate_final`: 검증할 이번 임시답변 전체 문자열.
- `flagged_numeric_or_date_values`: 숫자/날짜 정규화가 미일치를 발견할 경우에만 존재.

`user_evidence`와 `verified_prior_conversation` 배열은 JSON에서 제거했다. 그러나 기존 검증 통과 출처의 신뢰 여부는 `verified_prior`로 그대로 구분한다. 숫자 비교에서도 이전 승인된 어시스턴트 답변과 현재 사용자 발화만 근거로 인정한다.

실패한 툴의 `result`는 실패 사실을 설명하는 자료이지 성공한 실행 결과에 대한 근거가 아니다. 에러 결과는 숨기지 않으며 성공 근거와 구분한다.

## 확인

```bash
python -m pytest -q tests/test_verifier_compact_context_contract.py tests/test_verifier_accepted_conversation.py tests/test_final_grounding_verifier.py tests/test_verifier_restored_factual_contract.py tests/test_temporal_grounding_prompts.py
python -m pytest -q
```

`think=true`는 별도 실험 항목이며 본 PR에서는 변경하지 않았다.
