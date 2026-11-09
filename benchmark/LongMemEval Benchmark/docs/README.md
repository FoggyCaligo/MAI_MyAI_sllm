# LongMemEval-S — MAI · OpenClaw 장기기억 평가

## 목적과 현재 구현 범위

LongMemEval은 GAIA와 달리 **질문에 앞서 주어진 과거 대화 전체를 기억에 적재**해야 한다. 단순히 질문에 과거 대화를 복사해서 붙여 넣으면 장기기억 시스템이 아닌 긴 문맥 입력 능력을 평가하게 된다.

이 첫 단계에서는 MAI의 기존 `ChatSessionStore`, `MemoryRuntime.extract_facts`, `record_raw_user_evidence`, `finish_turn`, `run_user_message`를 그대로 사용해 **원본 대화를 순서대로 오프라인 재생하는 실행기**를 구현했다. 기억 전용 DB, 대화 DB, 문장 분리 DB, 작업 디렉터리는 질문마다 별도로 생성된다. 과거 대화가 다른 문항의 점수에 영향을 주지 않게 하기 위함이다.

**OpenClaw용 원본 대화 재생 어댑터도 구현했다.** OpenClaw의 플러그인 SDK가 제공하는 세션 생성과 `withSessionTranscriptWrite`를 사용하여 `user`·`assistant` 원문을 순서대로 기록한다. 파일로 기억을 만들어 넣거나 내부 SQLite에 SQL을 직접 실행하지 않는다. 기록 뒤에는 세션을 다시 읽어 역할·내용·메시지 수를 검증하고, `memory-core`의 세션 기록 검색을 활성화해 새 질문으로 평가한다. **OpenClaw 실제 Windows 실행은 아직 검증되지 않았다.**

다만 MAI는 원본 발화와 답변을 사용해 모델 기반 사실 추출까지 수행하고, OpenClaw는 원본 대화를 네이티브 세션 저장소에 기록하고 해당 세션 기록을 검색한다. 이는 같은 입력·같은 모델에서 *기본 제품의 기억 시스템*을 비교하는 프로토콜이며 **기억 생성 알고리즘만 통제한 동일 조건 실험은 아니다.**

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

### 2-1. OpenClaw에서 동일 문항 평가

공식 [OpenClaw 설치 안내](https://docs.openclaw.ai/install)에 따라 Windows CLI를 설치하고 `openclaw --version`이 성공하는지 확인한다. **MAI와 OpenClaw를 순차적으로 평가한다.** Ornith의 동일한 GGUF와 8080번 `llama.cpp` 라우터를 사용하며 별도의 클라우드 모델을 연결하지 않는다. 위 2절의 `-RouterOnly`를 실행한 상태여야 한다.

```powershell
& ".\\portable\\portable-runtimes\\python\\python.exe" `
  ".\\benchmark\\LongMemEval Benchmark\\adapter\\run_openclaw.py" `
  --dataset ".\\benchmark\\LongMemEval Benchmark\\data\\longmemeval_s_cleaned.json" `
  --start 0 --limit 1 `
  --output-dir ".\\benchmark\\LongMemEval Benchmark\\result\\openclaw_trial_001"
```

이 명령은 **원본 역할이 보존된 대화 기록을 OpenClaw의 네이티브 세션에 주입**하기 위한 벤치마크 전용 로컬 플러그인을 사용한다. `openclaw-plugin/index.ts`는 OpenClaw의 공식 Plugin SDK `createSessionEntry` / `withSessionTranscriptWrite` / `readVisibleSessionTranscriptMessageEntries` API를 사용한다. 설치된 OpenClaw 버전에서 플러그인 API가 달라지면 실패한다. 호출은 다음 순서다.

1. 질문마다 새 `state/`, `workspace/` 디렉터리 및 해당 모델 전용 `openclaw.json`을 준비한다.
2. `OPENCLAW_STATE_DIR`·`OPENCLAW_CONFIG_PATH`를 **평가 폴더로만 설정한 자식 프로세스**에서 `openclaw longmemeval import --input history.json`을 호출한다. 플러그인은 원본 대화의 역할·메시지 수를 검증하고, 원래 세션 하나마다 새 세션을 만든다. Gold answer·`has_answer`·정답 세션 ID는 `history.json`에 포함되지 않는다.
3. `openclaw memory index --force --agent main`으로 네이티브 **세션 기록 인덱스**를 갱신한다. 비교의 로컬 모델 제약을 지키기 위해 `memory.search.provider = "none"`으로 고정하며, **의도적인 FTS 전용 모드**임을 기록한다. MAI의 기억 검색과 임베딩 방식이 같다는 뜻은 아니다.
4. `openclaw agent exec --state-dir ... --config ... --model mai-local/<GGUF>`로 별도 신규 세션에서 평가 질문의 원문만 전달한다. 원본 과거 대화를 질문 프롬프트에 붙이지 않는다.
5. `predictions.jsonl`에 응답·상태·걸린 시간·메시지 수를 기록한다. 전체 실행 이력과 CLI 표준 출력·오류는 문항별 폴더에 보존한다. 역할 검증 실패·플러그인 부재·기억 인덱싱 실패·모델 호출 실패 시 **해당 문항을 실패로 기록하고 즉시 중단**한다.

사용자 홈의 기존 `~/.openclaw` 기억 데이터를 벤치마크에 사용하지 않기 위해, 케이스별 전용 상태 디렉터리를 지정한다. 단, `openclaw agent exec`는 설정한 `--cwd` 밖으로의 OS 파일 접근이 완전히 샌드박싱된다는 뜻이 아니다. **별도 시험용 Windows 계정 또는 VM에서 실행**해야 한다. 인터넷 검색이나 추가 API 키가 필요한 기능을 평가에 섞지 않는다.

1문항 종단간 검증에 성공하면 `--limit 10`, 전체 500문항은 `--limit 0`을 사용하고 **새 `--output-dir`**를 지정한다. 이 플러그인은 별도 상용 서비스나 일반 사용을 목적으로 설치하는 것이 아니며, 벤치마크 전용 `openclaw.json`에서만 명시적으로 로딩한다.

**점수 주의:** 두 어댑터가 만드는 `predictions.jsonl`은 실행 결과이며, 공식 LongMemEval의 의미 평가 점수는 아니다. 공식 채점기 연결 전에는 전체 정답률을 공식 수치로 보고하지 않는다.

### 3. 검증

```powershell
& ".\portable\portable-runtimes\python\python.exe" -m pytest -q tests/test_longmemeval_benchmark.py
```

OpenClaw 어댑터 계약 테스트도 별도로 추가했다. 아래 명령은 실제 OpenClaw 실행 없이 원본 역할·입력 격리·출력 계약만 검사한다.

```powershell
& ".\\portable\\portable-runtimes\\python\\python.exe" -m pytest -q tests/test_longmemeval_openclaw_adapter.py
```

기존 MAI 테스트는 데이터셋 역할·시간 순서·정답 메타데이터 비노출 계약을 검증한다. 사용자 PC에서 실제 모델과 임베딩/기억을 포함한 종단간 테스트는 **아직 수행되지 않았다**.

## 점수 산정과 보완 과제

`predictions.jsonl`은 질문 ID, 질문 유형, 상태, 모델 답변, 원본 대화 쌍 개수, 시간 및 오류를 기록한다. **현재 공식 LongMemEval 채점기를 연결하지 않았다.** 문자열 완전 일치만으로 LongMemEval의 의미적 정답성을 평가해서 공식 점수라고 부르지 않는다.

다음은 별도 구현이 필요한 항목이다.

1. OpenClaw의 실제 Windows 환경에서 플러그인 CLI 로딩·세션 저장·인덱스 생성·신규 세션 회상을 1문항으로 검증
2. 공식 평가 코드와 평가 모델 계약 확인 후 MAI·OpenClaw **공통 채점기** 추가
3. 기억 적재 시간·답변 시간·인덱스 크기·모델 호출 수 분리 기록
4. 모델 호출 없이 재생되는 과거 어시스턴트 턴, 원본 툴 결과 부재, 각 제품 기억 저장·검색 알고리즘의 차이를 최종 비교표에 함께 공개

**이 벤치마크는 MAI/ OpenClaw의 기본 기억 엔진을 비교하는 평가이지 완전 동일한 내부 추출 파이프라인 비교가 아니다.**
