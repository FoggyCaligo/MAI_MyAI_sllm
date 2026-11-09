# LongMemEval-S — MAI · OpenClaw 장기기억 평가

## 목적과 현재 구현 범위

LongMemEval은 GAIA와 달리 **질문에 앞서 주어진 과거 대화 전체를 기억에 적재**해야 한다. 단순히 질문에 과거 대화를 복사해서 붙여 넣으면 장기기억 시스템이 아닌 긴 문맥 입력 능력을 평가하게 된다.

이 첫 단계에서는 MAI의 기존 `ChatSessionStore`, `MemoryRuntime.extract_facts`, `record_raw_user_evidence`, `finish_turn`, `run_user_message`를 그대로 사용해 **원본 대화를 순서대로 오프라인 재생하는 실행기**를 구현했다. 기억 전용 DB, 대화 DB, 문장 분리 DB, 작업 디렉터리는 질문마다 별도로 생성된다. 과거 대화가 다른 문항의 점수에 영향을 주지 않게 하기 위함이다.

현재는 **MAI 실행기만 구현**했다. OpenClaw의 일반 `agent exec` 인터페이스는 한 번의 사용자 요청 실행용이므로, LongMemEval 원본 기록에 들어 있는 과거 `assistant` 역할의 메시지를 그대로 저장·재생시키는 계약을 확인하기 전까지는 OpenClaw용 평가기를 제공하지 않는다. 과거 Assistant 발화를 사용자 발화로 둔갑시키거나, 수십 회의 대화를 단일 요약 프롬프트로 바꿔 입력하면 동등 비교가 아니다. 따라서 현재 코드만으로는 **MAI vs OpenClaw LongMemEval 점수 비교가 완성되지 않는다.**

## 데이터셋 및 입력 계약

원본: [LongMemEval 공식 저장소](https://github.com/xiaowu0162/LongMemEval), 정리된 공개 데이터: [xiaowu0162/longmemeval-cleaned](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned).

- 기본 대상은 `longmemeval_s_cleaned.json`이다. 전체 문항 수는 500개다.
- 각 문항의 `haystack_session_ids` / `haystack_dates` / `haystack_sessions`를 동기화 검증한다.
- `haystack_sessions` 안의 각 턴을 **원본 `role`, `content` 그대로** 사용한다. `has_answer`와 `answer_session_ids`, 정답 `answer` 등은 모델에 전달하지 않는다.
- 사용자·어시스턴트 **완료된 원본 대화 쌍** 하나마다 MAI의 기억 추출 및 저장 경로를 실행한다. 원본에는 별도의 tool 결과가 없으므로 `successful_tool_results=()`를 사용한다. 존재하지 않는 툴 로그를 재구성하지 않는다.
- 원본 대화 역할이 짝을 이루지 않거나, 세션 날짜 순서가 맞지 않으면 **실패**로 처리하며 임의의 어시스턴트 답변을 생성하지 않는다.
- MAI의 추출 경로가 보통 백그라운드 처리 과정에서 기록하고 넘기는 오류도 본 평가에서는 **즉시 실패**로 처리한다. 추출 실패를 무해한 상황으로 숨기지 않는다.
- 원본 세션의 날짜를 기억 엔진의 `now`에 적용한다. 단, 원본 세션 안 여러 턴의 개별 초 단위 시각은 제공되지 않아 세션 날짜를 공통 적용한다.
- 모든 과거 대화 입력을 마친 후, 질문 문항의 `question`만 **새 문맥**으로 MAI의 기존 에이전트에 전달한다. 검색할 수 있는 데이터는 독립 기억 DB에 있는 이전 기록뿐이다.
- 질문별로 데이터셋 전체를 다시 적재하는 구조다. 500개 전체를 평가하면 역사 대화 적재 비용이 매우 크다. 속도 지표는 적재 시간과 답변 시간을 분리해 해석해야 한다.

## 실행 환경

**독립 Windows VM 또는 시험용 OS 계정에서 실행한다.** 평가 실행기는 MAI의 기존 ToolRegistry를 사용하므로, 평가 문제의 지시에 따라 로컬 셸·파일·네트워크 도구를 사용할 수도 있다. 단지 `cwd`를 시험 폴더로 지정하는 것으로는 OS 파일 접근을 격리할 수 없다. 개인 소유 파일과 자격 증명을 같은 계정에서 노출하지 않는다.

같은 Ornith GGUF와 동일한 llama.cpp router를 사용하되, 기존 사용자 메모리 DB와 절대 경로를 공유하지 않는다. 서버는 MAI 웹 앱이 없는 **router-only** 모드로 실행할 수 있다(PR #520의 `-RouterOnly`가 `dev`에 병합된 경우).

### 1. 데이터 다운로드

공식 LongMemEval-S 파일을 HTTP GET으로 직접 스트리밍하여 다운로드한다. 이전 `huggingface_hub` 메타데이터 HEAD 요청은 사용자 환경에서 HTTP 400으로 실패했다. 이번 코드는 동일한 공식 URL에 프로젝트 식별 User-Agent를 보내며, 다운로드한 파일의 길이(277,383,467바이트)와 SHA-256을 검증한 뒤 최종 경로로 원자적 이동한다. **2026-11-09 사용자 Windows 환경에서 GET 다운로드 성공을 확인했다.** 실패 시 HTTP 예외가 그대로 표시되며, 잘못 받은 파일을 정상 데이터로 취급하지 않는다.

```powershell
& ".\benchmark\GAIA Benchmark\adapter\.venv\Scripts\python.exe" `
  ".\benchmark\LongMemEval Benchmark\adapter\download.py"
```

다운로드 파일은 `benchmark/LongMemEval Benchmark/data/longmemeval_s_cleaned.json`에 저장되고 Git에서 제외된다.

### 2. 원본 대화 재생과 1문항 평가

**먼저 Ornith 모델용 llama.cpp 라우터를 실행한다.** 저장소 루트의 첫 번째 PowerShell에서 아래 명령을 실행하고 해당 창을 열어 둔다. `-RouterOnly`는 MAI 웹 서버를 시작하지 않고 8080번 라우터만 실행한다. GPU와 모델 상주는 기존 Portable 설정을 따른다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\\portable\\start-portable-llamacpp-router.ps1" -RouterOnly
```

두 번째 PowerShell에서 다음 명령으로 모델 등록 여부와 상태를 확인한다.

```powershell
(Invoke-RestMethod "http://127.0.0.1:8080/models").data |
  Select-Object id, status
```

모델 목록이 조회되지 않으면 평가를 시작하지 않는다. 등록된 모델이 있어도 실제 적재에 실패할 수 있으므로 오류가 발생하면 **첫 번째 PowerShell의 llama.cpp 로그**에서 로딩 실패 원인을 확인해야 한다. `RouterModelTransitionError`는 모델 전환 실패를 뜻하며, 이것만으로 VRAM 부족·연결 실패 중 하나로 단정할 수 없다.

이후 두 번째 PowerShell에서 평가를 실행한다:

```powershell
& ".\portable\portable-runtimes\python\python.exe" `
  ".\benchmark\LongMemEval Benchmark\adapter\run_mai.py" `
  --dataset ".\benchmark\LongMemEval Benchmark\data\longmemeval_s_cleaned.json" `
  --start 0 --limit 1 `
  --output-dir ".\benchmark\LongMemEval Benchmark\result\mai_trial_001"
```

**빈 결과 폴더만 허용**하며, 이미 존재하는 폴더·기억 DB를 덮어쓰지 않는다. 문항마다 독립 DB를 만들고, 원본의 user/assistant 쌍을 한 번씩 `finish_turn`으로 적재한다. 이후 MAI는 질문에 답한다. 실패한 문항은 에러와 함께 결과를 기록하고 실행을 중단한다.

검증이 완료되면 `--limit 10`으로 증가시키고, 전체 평가에는 `--limit 0`을 사용한다. 새 실행마다 서로 다른 `--output-dir`를 지정한다. 문항별 기록은 `predictions.jsonl`이며 정답을 넣지 않는다.

### 3. 검증

```powershell
& ".\portable\portable-runtimes\python\python.exe" -m pytest -q tests/test_longmemeval_benchmark.py
```

이 테스트는 데이터셋 역할·시간 순서·정답 메타데이터 비노출 계약만 검증한다. 사용자 PC에서 실제 모델과 임베딩/기억을 포함한 종단간 테스트는 **아직 수행되지 않았다**.

## 점수 산정과 보완 과제

`predictions.jsonl`은 질문 ID, 질문 유형, 상태, 모델 답변, 원본 대화 쌍 개수, 시간 및 오류를 기록한다. **현재 공식 LongMemEval 채점기를 연결하지 않았다.** 문자열 완전 일치만으로 LongMemEval의 의미적 정답성을 평가해서 공식 점수라고 부르지 않는다.

다음은 별도 구현이 필요한 항목이다.

1. OpenClaw의 기본 memory-core·세션 저장소에 **원래 user와 assistant 역할 그대로** 시간순 주입할 수 있는 지원된 API 확인 및 어댑터 구현
2. 동일 대화 주입 이후 OpenClaw와 MAI의 새 세션 질문 실행 및 동일 채점기 적용
3. 공식 평가 코드와 평가 모델 계약 확인 후 점수 산정, 비용·시간·저장 공간 분리 기록
4. 모델 호출 없이 재생되는 원본 어시스턴트 턴의 의미, 시대별 도구 결과 부재, 두 시스템의 기억 입장 정책 차이를 최종 점수와 함께 공개

**이 벤치마크는 MAI/ OpenClaw의 기본 기억 엔진을 비교하는 평가이지 완전 동일한 내부 추출 파이프라인 비교가 아니다.**
