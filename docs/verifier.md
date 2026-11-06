# MAI 최종 검증 — Granite 추출 + Tev System One 판단

최종 임시답변은 사용자가 확인하기 전에 검증한다. 성공하면 원문을 그대로 반환하고, 실패하면 해당 주장을 명시한 검증 결과를 기존 메인 Agent Loop에 전달해 추가 툴 조회·주장 축소·삭제를 수행한다. 검증 실패를 임의 승인하거나 기존 Verifier로 자동 우회하지 않는다.

## 구성·설정

- `VERIFIER_MODEL`: 임시답변에 포함된 검증 가능 진술을 **의미를 보존한 문장으로 추출**하는 모델. 기본 `granite4:3b-h`. 기존 설정 계약대로 생략되면 메인 모델이 추출을 담당한다.
- `VERIFIER_DECISION_MODEL`: Ollama System One 판단 모델. 기본 `tev1:4b`.
- `VERIFIER_DECISION_TIMEOUT_SECONDS`: Tev 단일 배치 HTTP 요청 제한 시간. 기본 120초.
- `VERIFIER_DECISION_BATCH_SIZE`: Tev 한 번의 System One 요청에 넣을 주장 수. `settings.yaml` 기본 30개. 1~64의 정수로 변경 가능하며 범위를 벗어나면 실행 실패.
- `OLLAMA_HOST`: System One 로컬 API 접속 호스트.
- `MAI_ANSWER_QUALITY_REVIEW`: 기본 false. true면 사실 판정 통과 뒤 기존 품질 검토를 별도 모델 호출로 진행한다.

**필수:** Ollama **0.35.0 이상** 및 다운로드한 `tev1:4b` 모델. `ollama --version`과 `ollama list`로 확인한다. Ollama의 `/v1/systemone`는 llama.cpp 네이티브 툴 호출과 별도 API이다. 판단 모델에 연결할 수 없으면 검증 실패를 명시적으로 드러낸다.

## 단계

1. Python은 기존 숫자·날짜 정규화 검사를 실행한다.
2. Granite에 추출 프롬프트와 **임시답변의 Markdown 파싱 결과(검증용 텍스트), 이전 문맥**만 전달한다. 툴 결과·사용법·검증 프롬프트 전문은 Granite 추출 입력에 포함하지 않는다. JSON **문자열 배열**만 수신한다. 임시답변과 정확히 같은 부분 문자열일 필요는 없으며, 원문과 의미·부정·수치·단위·시점·조건이 동일한 재서술을 허용한다. **빈 배열을 곧바로 성공으로 간주하지 않고**, 별도의 Granite 구조화 요청으로 후보 답변 전체에 검증 가능한 진술이 하나라도 있는지 명시적으로 재판정한다. `contains_checkable_assertions`이면 후보 원문에 존재하는 witness span을 요구하고 누락 추출 계약 오류로 종료한다. `no_checkable_assertions`이면 witness=null과 비어 있지 않은 사유를 요구한다. 응답 형식 위반은 오류로 유지하고, 실패를 승인하거나 묵시적으로 다른 Verifier로 전환하지 않는다. 추출 결과가 비어 있지 않은 문자열인지 확인한다. 의미 일치 여부는 Python 문자열 대조가 아니라 Tev 판정에서 원본 검증용 텍스트(`orig_answer`)와 각 추출 주장을 대조한다. 원문에 없는 주장이나 의미가 달라진 주장은 `insufficient`로 분류한다. Python에서 미확인 수치로 표시한 값이 전부 추출되었는지도 확인하고, 누락되면 명확하게 실패한다.
3. Python이 추출 목록에 순서대로 `c0`, `c1`, … ID를 부여한다. 설정된 `VERIFIER_DECISION_BATCH_SIZE`개씩 나눠 순차 실행한다.
4. 각 Tev 배치는 `state={orig_answer: 전체 검증용 텍스트, source: {datetime_local_iso, conversation_context, tool_results_in_execution_order}}`와 `questions={c0:..., ...}`를 **각각 한 번씩** 전달한다. 배치 내부 질문들은 같은 state를 공유한다.
5. Tev는 우선 추출 문장이 원본 답변에 **의미상 존재하는 주장인지** 검토하고, 이어서 근거와 일치하는지 판정한다. 원본에 없는 주장·의미가 달라진 주장은 `insufficient`로 판정한다. Tev의 choice 판정은 `supported / contradicted / insufficient` 중 하나. 누락·중복·예상치 못한 질문 ID나 잘못된 판정은 오류다. `insufficient`는 반증이 아니라 관측 근거의 불충분을 의미한다.
6. Python은 모든 배치의 판정을 수집한 뒤 각각의 원문 주장에 다시 연결한다. `contradicted`와 `insufficient`를 기존 Verifier의 `unsupported`로 바꾸되 사유는 구분한다. 하나라도 검증 불통과면 메인모델에 해당 주장과 사유를 전달한다.
7. 기존 숫자·근거·교정 한도와 교정된 임시답변의 전면 재검증 흐름을 유지한다.

질문이 설정된 배치 크기 이하면 Tev를 한 번 호출한다. 더 많으면 같은 크기로 순차 분할하고 마지막 배치는 남은 질문만 처리한다. Ollama API는 최대 64개 질문을 허용하므로 `VERIFIER_DECISION_BATCH_SIZE`는 1~64의 정수만 허용한다. 여러 질문의 state를 요청 내에서 한 번 제공하지만, **여러 배치 사이에서는 state를 다시 전송**한다. 공통 컨텍스트를 GPU 내부에서 한 번만 계산한다고 가정해서는 안 된다.

## 검증용 텍스트와 화면 표시

- 최종 답변·임시답변의 원본 Markdown은 변형하지 않는다. Granite와 Tev의 주장 추출·판단에만 Markdown 파서가 구성한 별도 텍스트를 사용한다.
- 강조·링크·목록 등의 표현 문법은 검증용 텍스트에서 제외하되, 화면에 표시되는 내용과 코드 블록의 실제 텍스트는 남긴다.
- 표는 셀만 병합하지 않고 **각 데이터 행에 표 머리글을 연결**한다. 예를 들어 `| 단어 | 철자 |`의 `| Ark | A-R-K |`는 `단어: Ark; 철자: A-R-K`로 전달한다. 원본 Markdown 표는 사용자에게 그대로 렌더링한다.
- 문자열 치환이나 의미 추측으로 임시답변을 고치지 않는다. 추출 문장이 원문의 부분 문자열이 아니어도 허용하며, 의미 일치 판단은 Tev에 맡긴다. 비어 있는 추출 문장은 계약 오류다.
- 실패 응답에 임시답변이 있고 마지막 verifier 피드백이 없더라도 임시답변은 Markdown으로 렌더링하고, 실제 오류 내용은 별도 영역에 표시한다. 재개·대화기록 화면에도 동일하게 적용한다.

## 오류와 근거의 범위

- Ollama 로컬 System One 입력은 텍스트·스키마 기준 최대 64KiB이며, 실제 모델 컨텍스트 제한도 지켜야 한다. 입력이 너무 길어지면 임의로 잘라서 성공으로 취급하지 않고 예외를 반환한다.
- `source`에는 기존 Verifier 입력 계약에 따라 검증 통과 대화 여부, 툴 실행 인자, `endtype`, 실행 결과가 포함된다. `endtype=error_type:<type>`인 결과는 실패 사실을 뒷받침할 수 있지만 성공 사실을 증명할 수 없다.
- 새로운 모델이 기존 내용 전체의 정확성을 독립적으로 보장하는 것은 아니다. 특히 Granite가 수치 이외의 주장을 빠뜨리는 경우 누락 주장은 Tev가 검증하지 못하며, Tev의 의미 일치 판정 또한 오류 가능성이 있다. 이 구조는 실사용 전 MAI의 실제 과잉거절/할루시네이션 로그에서 평가가 필요하다.
- 긴 툴 로그 전체가 입력 한도를 넘으면 명시적으로 실패한다. 이 버전은 임의 근거 발췌, 문맥 추측, 자동 압축 우회를 구현하지 않는다.
- `Tev1`의 분류 확률은 정답의 객관적 확률이 아니다. Python은 점수 임계값으로 임의 판정하지 않고 모델의 명시적인 choice만 사용한다.
- 모든 내부 Tool Registry, Agent Skills, 권한, 실패 처리와 사용자에게 반환되는 최종 검증 계약은 그대로 유지한다.

## 감사 이벤트와 테스트

Granite의 원본 추출 요청·응답은 기존 `request/raw_response/validated_response` 단계(`stage=extract`)로 감사 DB에 기록한다. 추출이 빈 경우에만 추가된 독립 판정은 `stage=empty_extraction`의 `request/raw_response/validated_response`에 남긴다. Tev는 배치마다 `stage=decision`의 `request`와 `validated_response`를 기록한다. 마지막 `decision`에는 Python이 집계한 주장별 결과가 기록된다.

```bash
ollama --version
ollama pull tev1:4b
python -m pytest -q tests/test_decision_verifier.py tests/test_verifier_empty_claim_contract.py tests/test_verifier_markdown_canonical.py tests/test_verifier_model_setting.py
python -m pytest -q
```

모델 로딩/전환 시간, 총 처리 시간, 잘못된 승인, 과잉거절, 추출 누락률을 **기존 Granite 단독**과 실제 로그로 비교하기 전에는 속도나 정확도 개선을 확정하지 않는다.

Portable 배포는 신규 매니페스트 생성 시 Ollama v0.35.0 이상을 검증하고 Tev1을 기본 모델에 포함한다. 기존 설치본을 갱신할 때는 `portable/README.md`에 설명한 `-UpgradeOllama`와 고정 해시 매니페스트를 사용한다.
