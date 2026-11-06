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
2. Granite에 **임시답변의 Markdown 파싱 결과(검증용 텍스트), 이전 대화 문맥**만 전달한다. 도구 근거나 별도의 검증 판단은 추출 단계에 넣지 않는다. 출력 계약은 **최상위 배열 하나 안에 12개의 문자열 배열**을 포함하는 JSON이며, 위치는 프롬프트의 1~12번 분류 순서다. 각 분류에 해당 주장이 없으면 그 위치를 빈 배열로 둔다. 원문과 의미·부정·수치·단위·시점·조건을 보존한 재서술을 허용한다. 각 분류의 주장들을 순서대로 펼쳐 Tev에 전달한다. 빈 추출에 대한 **추가 LLM 호출은 수행하지 않는다**. 추출 JSON 형태가 잘못되거나 빈 문자열이 들어 있으면 계약 오류로 실패한다. 추출한 문장이 원문에 의미상 존재하는지는 Tev가 원본 답변과 함께 판단한다. 기존 숫자·날짜 검사는 유효하며, 검사에 표시된 수치가 추출에서 빠졌으면 명확하게 실패한다.
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
- 문자열 치환이나 의미 추측으로 임시답변을 고치지 않는다. 추출 문장이 원문의 부분 문자열이 아니어도 허용하며, 의미 일치 판단은 Tev에 맡긴다. 비어 있는 문자열 원소는 계약 오류지만, 개별 분류의 빈 배열은 허용한다.
- 실패 응답에 임시답변이 있고 마지막 verifier 피드백이 없더라도 임시답변은 Markdown으로 렌더링하고, 실제 오류 내용은 별도 영역에 표시한다. 재개·대화기록 화면에도 동일하게 적용한다.

## 오류와 근거의 범위

- Ollama 로컬 System One 입력은 텍스트·스키마 기준 최대 64KiB이며, 실제 모델 컨텍스트 제한도 지켜야 한다. 입력이 너무 길어지면 임의로 잘라서 성공으로 취급하지 않고 예외를 반환한다.
- `source`에는 기존 Verifier 입력 계약에 따라 검증 통과 대화 여부, 툴 실행 인자, `endtype`, 실행 결과가 포함된다. `endtype=error_type:<type>`인 결과는 실패 사실을 뒷받침할 수 있지만 성공 사실을 증명할 수 없다.
- 새로운 모델이 기존 내용 전체의 정확성을 독립적으로 보장하는 것은 아니다. 특히 Granite가 수치 이외의 주장을 빠뜨리는 경우 누락 주장은 Tev가 검증하지 못하며, 특히 모든 분류가 빈 배열이면 TEV1 질문도 생성되지 않는다. Tev의 의미 일치 판정 또한 오류 가능성이 있다. 이 구조는 실사용 전 MAI의 실제 과잉거절/할루시네이션 로그에서 평가가 필요하다.
- 긴 툴 로그 전체가 입력 한도를 넘으면 명시적으로 실패한다. 이 버전은 임의 근거 발췌, 문맥 추측, 자동 압축 우회를 구현하지 않는다.
- `Tev1`의 분류 확률은 정답의 객관적 확률이 아니다. Python은 점수 임계값으로 임의 판정하지 않고 모델의 명시적인 choice만 사용한다.
- 기존 `dev`의 `tool_manual` 방식, Tool Registry, 모델 thinking 전달, 권한 및 도구 실패 처리는 유지한다. 검증 모듈과 검증에 전달하는 실행 근거의 구조만 이식한다.

## 감사 이벤트와 테스트

Granite의 원본 추출 요청·응답은 기존 `request/raw_response/validated_response` 단계(`stage=extract`)로 감사 DB에 기록한다. `stage=empty_extraction`은 삭제하여 별도 모델 호출이나 감사 이벤트를 만들지 않는다. Tev는 배치마다 `stage=decision`의 `request`와 `validated_response`를 기록한다. 마지막 `decision`에는 Python이 집계한 주장별 결과가 기록된다.

```bash
ollama --version
ollama pull tev1:4b
python -m pytest -q tests/test_decision_verifier.py tests/test_verifier_empty_claim_contract.py tests/test_verifier_markdown_canonical.py tests/test_verifier_model_setting.py
python -m pytest -q
```

모델 로딩/전환 시간, 총 처리 시간, 잘못된 승인, 과잉거절, 추출 누락률을 **기존 Granite 단독**과 실제 로그로 비교하기 전에는 속도나 정확도 개선을 확정하지 않는다.

이 `dev` 기반 이식에서는 기존 Portable 및 Windows 설치 스크립트를 변경하지 않는다. 따라서 별도로 Ollama v0.35.0 이상과 `tev1:4b` 설치 여부를 확인해야 한다. 현재 설치 스크립트가 System One 필수 조건을 자동 충족한다고 가정하지 않는다.
