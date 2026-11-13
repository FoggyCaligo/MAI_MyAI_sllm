# GAIA Benchmark 실행 안내

MAI의 범용 에이전트 능력을 GAIA 2023으로 검증하기 위한 **로컬 전용** 실행 도구다. GAIA 데이터의 재배포가 제한되어 있어 원본 문제·정답·첨부파일은 Git에 넣지 않는다.

## 폴더 구성

```text
benchmark/
├─ .gitignore
├─ agents/                         # 비교용 소스 다운로드 후 생성(추적 안 함)
└─ GAIA Benchmark/
   ├─ docs/
   │  ├─ README.md
   │  ├─ setup.ps1 / setup.sh
   │  ├─ start_benchmark_server.ps1
   │  └─ download_agents.ps1
   ├─ adapter/
   │  ├─ download_gaia.py
   │  ├─ run_gaia.py
   │  ├─ resolve_main_model.py
   │  ├─ score_gaia.py
   │  └─ data/gaia/                 # 인증 후 데이터 자동 다운로드(추적 안 함)
   └─ result/                       # 실행 결과(추적 안 함)
```

## 1. Hugging Face 접근 권한과 토큰 설정

GAIA 원본 문제·정답·첨부파일은 접근 승인이 필요한 데이터다. **처음 다운로드하기 전에** 직접 접근 권한을 확보한다. 해당 권한은 MAI 자체의 Owner 계정과 별개다.

1. [Hugging Face](https://huggingface.co/)에서 로그인한다.
2. [GAIA 데이터셋](https://huggingface.co/datasets/gaia-benchmark/GAIA)에 접속해 이용 조건에 동의하고 접근 권한을 확인한다.
3. [Access Tokens](https://huggingface.co/settings/tokens)에서 본인에게 적절한 읽기 권한의 토큰을 만든다. 가능한 경우 GAIA 데이터셋 접근만 허용한다.
4. 프로젝트 루트의 Git 추적 제외 파일 `.env`에 `HF_TOKEN`을 추가한다. **실제 토큰 값을 Git, 스크린샷, 채팅 또는 일반 문서에 공개하지 않는다.**

```dotenv
HF_TOKEN=YOUR_HUGGING_FACE_READ_TOKEN
```

`adapter/download_gaia.py`는 프로젝트 루트 `.env`를 자동으로 읽으며, 이미 프로세스 환경변수로 설정한 `HF_TOKEN`을 덮어쓰지 않는다. 따라서 Windows PowerShell과 Linux에서 별도로 토큰을 입력하거나 `hf auth login`을 실행할 필요가 없다. 다운로드용 토큰은 로컬 GAIA 데이터를 실행하거나 채점할 때 필요하지 않다. 기존 Hugging Face CLI 로그인도 계속 사용할 수 있다.

**인증 정보 보호:** `.env`는 `.gitignore`에 포함되어 있지만, 공개 저장소에 강제로 추가하지 않는다. 유출된 토큰은 Hugging Face에서 즉시 폐기·재발급한다. GAIA 데이터에 접근할 수 있는 권한이 없는 토큰은 다운로드에 실패한다.

**데이터 주의:** GAIA 원본 및 정답·첨부파일을 본체나 Memory Git 저장소에 커밋하지 않는다. 메타데이터 필드는 현재 어댑터가 사용하는 `task_id`, `Question`, `Level`, `Final answer`, `file_path`를 따른다.

## 2. Windows 자동 설정

저장소 루트의 PowerShell에서:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\benchmark\GAIA Benchmark\docs\setup.ps1"
```

위 명령은 새 PowerShell 프로세스에만 실행 정책 `Bypass`를 적용하며 시스템 전체 정책을 변경하지 않는다.

스크립트는 `adapter/.venv`를 만들고 Python 패키지(`python-dotenv` 포함)를 설치한 후 GAIA 데이터를 다운로드한다. 기존 MAI 모델 설치를 변경하지 않는다. 설치·인증 실패 시 명시적으로 중단한다.

Ubuntu Linux에서는 `sh "benchmark/GAIA Benchmark/docs/setup.sh"`로 데이터와 어댑터 가상환경을 준비할 수 있다. **현재 저장소에는 GAIA 평가 서버용 Linux 전용 시작 스크립트가 없다.** 아래 `start_benchmark_server.ps1`은 Windows PowerShell용이며, Linux에서 동일 격리 환경변수와 `MAIN_MODEL`을 준비하지 않은 채 그대로 명령을 변형해 실행하면 안 된다.

데이터 다운로드에는 Hugging Face에 접근할 수 있는 인터넷 연결이 필요하며, 인증 전에는 자동으로 약관을 대신 동의하지 않는다.

## 3. MAI 벤치마크 전용 서버

**일반 MAI(`./mai.sh start`)를 따로 실행하지 않는다.** GAIA 전용 스크립트가 기존 Portable 런처를 사용해 **llama.cpp router 한 개(보호 기능 사용 시 내부 8081)**를 시작하고, 정상 준비를 확인한 뒤 **GAIA 전용 MAI 웹 서버 한 개(8010)**를 실행한다. 일반 MAI 서버(8000)는 시작하지 않는다. 이 명령은 이전에 실행 중인 Router 또는 게이트웨이(8080/8081)가 있으면 중복 실행하지 않고 명시적으로 실패한다. 따라서 먼저 일반 MAI와 기존 router를 종료한다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\benchmark\GAIA Benchmark\docs\start_benchmark_server.ps1"
```

보호 기능이 켜져 있으면 이 한 명령으로 `llama-server` **1개**, 공통 메모리 게이트웨이 **1개**, MAI 평가용 서버 프로세스 **1개**가 시작된다. 보호 기능을 끄면 게이트웨이 없이 기존 Router 8080과 평가 서버 8010을 사용한다. 모델 상주 제한 `--models-max 1`도 유지한다. 서버는 `http://127.0.0.1:8010`에 바인딩하며 채팅·메모리·로그·문장 분리 DB를 `result/_runtime/`에 분리한다. **업로드 디렉터리, 파일·터미널·네트워크 도구의 OS 접근 권한까지 분리하는 스크립트는 아니다.** 원격 공개 호스팅을 끈다. 메인 모델 ID는 Portable 실행기와 같은 GGUF 카탈로그를 읽어 자동으로 설정하며, 기본 GGUF 파일이 없거나 손상된 경우 명확히 실패한다. 일반 MAI용 `.env`의 비밀번호는 변경하지 않는다.

로그인 계정은 MAI 서버의 기존 `OWNER_USERS` 환경 설정을 사용한다. 평가 실행기 역시 루트 `.env`의 `OWNER_USERS`를 읽어 인증 정보를 자동 선택한다. 계정이 하나라면 ID와 비밀번호를 입력할 필요가 없고, 계정이 여러 개면 `MAI_BENCH_USER` 또는 `--user`로 사용할 Owner를 명시한다. `MAI_BENCH_PASSWORD` 환경변수가 설정된 경우 명시적 비밀번호를 우선 사용한다. 자격 증명은 결과 JSONL·manifest에 기록하지 않는다. `run_gaia.py`는 먼저 `GET /login/session`으로 사전 세션 토큰을 받고 `X-MAI-Preauth-Session` 헤더를 붙여 `POST /login`으로 로그인한다. 기존 사용자의 비밀번호를 벤치마크 명령 인수에 직접 넣지 않는다. **장기기억 데이터뿐 아니라 시험용 서버 프로세스 환경 및 브라우저 세션을 분리해야 오염 위험을 줄일 수 있다.**

평가 작업 중 실제 파일·인터넷·셸 명령을 실행할 수 있으므로, 개인 파일·Git 작업 디렉터리가 아니라 별도 Windows 사용자 또는 VM에서 실행하는 것이 더 안전하다. 이 스크립트는 DB 경로 분리만 수행하며 OS 권한을 제한하거나 파일 작업을 샌드박싱하지는 않는다.

## 4. GAIA Level 1에서 10문제 실행

새 PowerShell에서:

```powershell
# OWNER_USERS가 여러 명일 때만 다음 줄 사용:
# $env:MAI_BENCH_USER = "본인의-MAI-로그인-ID"
& ".\benchmark\GAIA Benchmark\adapter\.venv\Scripts\python.exe" ".\benchmark\GAIA Benchmark\adapter\run_gaia.py" --server http://127.0.0.1:8010 --split validation --level level1 --limit 10
```

먼저 한 문제만 실행할 경우 `--limit 1`을 사용한다. **GAIA 전용 서버 실행 창은 열린 상태로 유지**하고 다른 PowerShell에서 `Invoke-RestMethod http://127.0.0.1:8080/v1/models`로 router 모델 목록, `Invoke-RestMethod http://127.0.0.1:8010/health`로 시험 서버 상태를 각각 확인한다. 둘 중 하나라도 실패하면 벤치마크를 진행하지 않는다. MAI 로그인 비밀번호는 프로젝트 루트의 `.env`에서 자동으로 읽어오므로 별도 숨김 입력을 요구하지 않는다. `.env` 파일 또는 필요한 설정이 없으면 명시적으로 실패하며, 이전의 대화형 암호 입력 경로는 사용하지 않는다. 요청당 최대 실행 시간은 기본 1800초이며, `--timeout-seconds`로 변경할 수 있다. MAI 자체의 모델 호출 제한은 별도 적용된다.

최초 10문제가 정상 완료되면:

```powershell
& ".\benchmark\GAIA Benchmark\adapter\.venv\Scripts\python.exe" ".\benchmark\GAIA Benchmark\adapter\run_gaia.py" --server http://127.0.0.1:8010 --split validation --level all --limit 0
```

`--level all`은 전체 Validation 평가를 뜻한다. `--split test`는 제출용이며 공개 정답이 없어 로컬 채점이 불가능하다.

## 5. 결과 및 재개

실행 시 UTC 시각을 사용한 `result/gaia_YYYYMMDDTHHMMSSZ/` 폴더가 생성된다.

- `predictions.jsonl`: GAIA 제출 형식(`task_id`, `model_answer`)
- `execution.jsonl`: 작업별 성공/실패, 오류, 실행 시간, 모델·도구 호출 수
- `manifest.json`: 평가 조건
- `local_score.json`: 간이 채점을 별도 수행할 때만 생성

기록의 `completed`는 **MAI 실행 및 출력 형식 완료**를 의미하며 GAIA 정답 여부가 아니다. 정답률은 아래 별도 채점으로 확인한다. 서버가 실패해도 `predictions.jsonl`에는 해당 `task_id`와 빈 `model_answer`가 남고 `execution.jsonl`에는 실패 원인이 기록된다.

중단된 실행 재개:

```powershell
& ".\benchmark\GAIA Benchmark\adapter\.venv\Scripts\python.exe" ".\benchmark\GAIA Benchmark\adapter\run_gaia.py" --server http://127.0.0.1:8010 --split validation --level level1 --limit 10 --output-dir ".\benchmark\GAIA Benchmark\result\gaia_실행폴더"
```

같은 매개변수로 재개하면 완료된 `task_id`를 건너뛴다. **실패한 문제도 체크포인트에 기록하므로 재실행하려면 새로운 출력 폴더를 사용한다.** 실패를 성공으로 취급하지 않는다.

## 6. 간이 채점

```powershell
& ".\benchmark\GAIA Benchmark\adapter\.venv\Scripts\python.exe" ".\benchmark\GAIA Benchmark\adapter\score_gaia.py" ".\benchmark\GAIA Benchmark\result\gaia_실행폴더\predictions.jsonl" --level level1
```

이 채점은 양끝 공백 제거(`strip`)와 대소문자 통일(`casefold`)로 비교하는 **엄격한 완전 일치 참고 지표**다. 중간 공백·구두점·단위 등의 의미 동등성은 평가하지 않는다. `score_percent_on_attempted`는 기록된 예측 수를 분모로 사용하고, 데이터셋 전체 대비 수행 비율은 `coverage_percent`로 별도 보고한다. GAIA 공식 평가의 정규화 규칙과 동일하지 않으므로 공식 리더보드 점수로 발표하면 안 된다. 시험 문제의 정답이 공개되지 않은 `test`는 로컬 채점하지 않는다.

## 7. OpenClaw와 MAI의 동일 GGUF 비교

OpenClaw는 Windows PowerShell CLI를 지원한다. 공식 [Windows 설치](https://docs.openclaw.ai/platforms/windows) 및 [`agent exec` 명령](https://docs.openclaw.ai/cli/agent)을 참고한다. 이 절은 **OpenClaw를 Windows 네이티브 CLI로 실행**하는 첫 번째 비교안이며, WSL2 실험과 혼합하지 않는다.

### 7.1 OpenClaw 설치 및 버전 고정

공식 Windows PowerShell 설치 프로그램([설치 안내](https://docs.openclaw.ai/install))을 사용할 수 있다.

```powershell
iwr -useb https://openclaw.ai/install.ps1 | iex
openclaw --version
```

인터넷에서 받아온 스크립트이므로 신뢰성을 검토한 뒤 실행한다. 설치만으로 API 모델이 자동 연결되는 것은 아니다. **OpenClaw의 클라우드 계정이나 추가 유료 API를 등록할 필요가 없는 GAIA 전용 어댑터**를 사용한다. `openclaw --version`의 출력은 벤치마크 `manifest.json`에 저장한다.

기존 `docs/download_agents.ps1`은 OpenClaw/Hermes **소스를 얕게 체크아웃만** 한다. 설치나 GAIA 실행을 수행하지 않으므로, 아래 OpenClaw CLI 어댑터와 역할이 다르다.

### 7.2 모델 서버: 하나만 사용

MAI와 OpenClaw를 동시에 실행하지 않는다. GAIA 서버를 띄우는 `start_benchmark_server.ps1`은 8010 MAI 서버까지 시작하므로 **OpenClaw 실험에서는 이 명령을 사용하지 않는다**. OpenClaw 실험에서는 Windows PowerShell을 별도로 열어 MAI Portable 라우터의 **단일 GGUF 모델 서버만** 실행한다.

현재 Portable 런처는 기본 실행 시 MAI 앱 서버를 함께 띄운다. OpenClaw 비교 시 MAI 서버를 띄울 필요 없이 router만 구동하려면 `portable/start-portable-llamacpp-router.ps1 -RouterOnly`를 사용한다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\portable\start-portable-llamacpp-router.ps1" -RouterOnly
```

별도 PowerShell에서 `Invoke-RestMethod http://127.0.0.1:8080/v1/models`로 모델 목록을 확인한다. OpenClaw 평가 실행기는 `resolve_main_model.py`를 호출해 MAI의 현재 GGUF 모델 별칭을 얻고, router의 실제 모델 목록에 없으면 실패한다. **동일한 GGUF SHA-256, llama.cpp 빌드, GPU layers, 컨텍스트 크기**를 사용하고 MAI 평가와 동시에 문제를 돌리지 않는다.

### 7.3 OpenClaw GAIA 한 문제 실행

이미 2절에서 GAIA 데이터를 다운로드했다면 재다운로드할 필요 없다. **실행기만 사용**하므로 MAI Owner ID/비밀번호 입력도 필요 없다.

```powershell
& ".\benchmark\GAIA Benchmark\adapter\.venv\Scripts\python.exe" `
  ".\benchmark\GAIA Benchmark\adapter\run_openclaw_gaia.py" `
  --split validation `
  --level level1 `
  --limit 1
```

OpenClaw CLI가 표준 PATH에 없는 환경에서는 `--openclaw`에 절대경로를 전달한다. 평가 어댑터는 다음을 수행한다.

- 동일 데이터셋의 동일 문제를 로드하고 `FINAL ANSWER:` 최종 줄 형식과 `score_gaia.py` 채점 계약을 재사용한다.
- `openclaw agent exec --json --config ... --model mai-local/<GGUF 별칭> --code-mode direct` 호출을 문제마다 별도 임시 상태로 실행한다. 별도의 OpenClaw Gateway를 요구하지 않으며 기존 개인 OpenClaw 대화 기록을 재사용하지 않는다.
- 8080 라우터만 연결하는 평가용 OpenClaw config를 `result/openclaw_*/openclaw.gaia.json`에 생성한다. 그 외 모델 제공자를 구성하지 않으며, 추가 모델 fallback도 제공하지 않는다.
- 문제 파일을 `result/openclaw_*/workspaces/<task_id>/`로 복사하고, 같은 디렉터리를 OpenClaw의 `--cwd`로 전달한다. 스크립트가 보장하는 것은 평가용 작업 디렉터리 분리이지 **OS 전체 파일 접근 차단이 아니다**.
- `result/openclaw_*/predictions.jsonl`은 MAI와 동일한 `task_id`, `model_answer`를 기록한다. `execution.jsonl`은 수행 시간, OpenClaw에서 관측 가능한 모델 라운드·도구 요약 등을 기록하며, 원본 CLI 출력과 표준 오류는 문제별 작업 폴더에 보존한다.
- 실패한 문제는 빈 답변으로 **실패 기록**하며 정상 완료나 정답으로 보고하지 않는다. 재개 시 기록된 실패도 건너뛴다. 실패 건 재시험은 새 출력 폴더에서 실행한다.

1문제 실행에 성공하면 `--limit 10`, Level 1 전체에는 `--limit 0`을 사용한다. 동일한 시작 인덱스(`--start`)와 정렬·버전을 유지해야 MAI와 1:1 비교가 된다.

### 7.4 OpenClaw 결과 채점

```powershell
& ".\benchmark\GAIA Benchmark\adapter\.venv\Scripts\python.exe" `
  ".\benchmark\GAIA Benchmark\adapter\score_gaia.py" `
  ".\benchmark\GAIA Benchmark\result\openclaw_실행폴더\predictions.jsonl" `
  --level level1
```

이 점수는 **로컬 GAIA Validation 엄격 일치 점수**이지 GAIA 공식 리더보드 점수가 아니다.

### 7.5 비교 해석 시 주의

모델·하드웨어·문제·시간 예산을 일치시키더라도 **두 에이전트의 도구와 기본 시스템 프롬프트는 다르다**. 따라서 결과는 동일 모델 환경에서 두 *프레임워크 전체의 성능* 비교이지 프롬프트만 분리한 원인 실험이 아니다. OpenClaw `agent exec`의 기본 도구는 MAI와 완전 동일하지 않으며, `--code-mode direct`를 사용한 프로파일을 명시적으로 기록한다. OpenClaw의 toolSummary는 내부 도구 이벤트를 포함할 수 있으므로 MAI의 도구 호출 숫자와 기계적으로 같은 단위라고 간주하지 않는다.

각 GAIA 문제에는 적대적 내용이 있을 수 있고 두 에이전트 모두 파일·네트워크·셸에 접근할 수 있다. **개인 Windows 계정에서 무제한 권한으로 실험하는 것은 권장하지 않는다.** 별도 OS 계정/VM에서 데이터 원본 및 개인 파일에 접근하지 못하도록 권한을 제한하는 것이 필요하다.

참고: [OpenClaw 로컬 커스텀 제공자](https://docs.openclaw.ai/concepts/model-providers/custom-providers), [OpenClaw agent exec](https://docs.openclaw.ai/cli/agent), [Windows 설치](https://docs.openclaw.ai/platforms/windows).

## 실행 옵션 전체 목록 (현재 adapter 기준)

실제 인자는 각 스크립트의 `--help`로 검증한다. **평가 실행기의 제한시간과 MAI 모델 API 호출 제한시간은 별개**이며, `--limit 0`은 선택된 범위의 전체 문제를 의미한다.

### MAI: `adapter/run_gaia.py`

| 인자 | 기본값 | 의미 |
|---|---|---|
| `--server` | `http://127.0.0.1:8000` | MAI API. **GAIA 격리 서버에서는 명시적으로 `http://127.0.0.1:8010` 입력** |
| `--split` | `validation` | `validation` / `test` |
| `--level` | `level1` | `level1`, `level2`, `level3`, `all` |
| `--limit` | `10` | 시작 인덱스부터 실행할 최대 문제 수; `0`이면 끝까지 |
| `--start` | `0` | 선택한 split/level에서 시작할 **0 기반** 인덱스 |
| `--timeout-seconds` | `1800` | **문제 하나당** GAIA 실행 한도(초) |
| `--poll-seconds` | `2.0` | 작업 완료 상태 조회 간격(초) |
| `--output-dir` | UTC 시각 기반 `result/gaia_*/` | 결과 및 체크포인트 폴더 |
| `--model` | 서버 기본 모델 | MAI에 넘길 등록된 GGUF 모델 ID |
| `--verify` | 미지정 / 비활성 | MAI의 최종 검증 기능을 **실험군에서만** 활성화 |
| `--user` | `MAI_BENCH_USER` 또는 단일 Owner 자동 선택 | Owner 로그인 ID |

`--verify`를 주지 않으면 평가 요청은 검증을 명시적으로 활성화하지 않는다. 검증 사용 여부는 **MAI와 OpenClaw의 프레임워크 비교 조건이 달라질 수 있으므로** 결과와 함께 기록한다. `--model`에는 사람이 보는 레이블이 아닌 Router에 등록된 정확한 ID를 사용한다.

예: Level 2 중 11번째부터 5문제를 별도 출력 폴더에서 실행:

```powershell
& ".\benchmark\GAIA Benchmark\adapter\.venv\Scripts\python.exe" `
  ".\benchmark\GAIA Benchmark\adapter\run_gaia.py" `
  --server http://127.0.0.1:8010 `
  --split validation --level level2 --start 10 --limit 5 `
  --timeout-seconds 1800 --poll-seconds 2 `
  --output-dir ".\benchmark\GAIA Benchmark\result\gaia_level2_sample"
```

### OpenClaw: `adapter/run_openclaw_gaia.py`

| 인자 | 기본값 | 의미 |
|---|---|---|
| `--split` | `validation` | `validation` / `test` |
| `--level` | `level1` | `level1`, `level2`, `level3`, `all` |
| `--limit` | `1` | 최대 문제 수; `0`이면 끝까지 |
| `--start` | `0` | 선택한 split/level의 0 기반 시작 인덱스 |
| `--timeout-seconds` | `1800` | OpenClaw **문제 하나당** 실행 제한(초) |
| `--openclaw` | `openclaw` | OpenClaw 실행 파일명 또는 실행 경로 |
| `--output-dir` | UTC 시각 기반 `result/openclaw_*/` | 출력 및 재개 폴더 |

OpenClaw 어댑터에는 MAI 전용 `--server`, `--model`, `--user`, `--verify`, `--poll-seconds` 옵션이 **없다**. 모델과 API는 자동 생성된 벤치마크 전용 설정으로 고정되며 엔드포인트는 `http://127.0.0.1:8080/v1`이다. 기존 OpenClaw 개인 설정과 벤치마크용 설정을 혼용하지 않는다.

예: MAI와 동일한 Level 2 범위 5문제 실행:

```powershell
& ".\benchmark\GAIA Benchmark\adapter\.venv\Scripts\python.exe" `
  ".\benchmark\GAIA Benchmark\adapter\run_openclaw_gaia.py" `
  --split validation --level level2 --start 10 --limit 5 `
  --timeout-seconds 1800 `
  --output-dir ".\benchmark\GAIA Benchmark\result\openclaw_level2_sample"
```

### Router / 메모리 보호 옵션 (공통)

`settings.yaml`에서 선택하며 런처 **재시작** 시 적용된다.

| 설정 | 현재 기본 구성 | 동작 |
|---|---|---|
| `LLAMACPP_MEMORY_GUARD_ENABLED` | `true` | 공통 RAM/VRAM 보호 게이트웨이 활성화; `false`는 비활성화 |
| `MAI_MIN_AVAILABLE_RAM_MIB` | `6144` | 모델 요청 전 최소 가용 RAM(MiB) |
| `LLAMACPP_MIN_FREE_VRAM_MIB` | `1024` | 예상 모델 로딩 후 최소 여유 VRAM(MiB); 별도 추가 오버헤드도 산정 |
| `LLAMACPP_CONTEXT_SIZE` | `65536` | 모델 컨텍스트 크기(토큰) |
| `MODEL_REQUEST_TIMEOUT_SECONDS` | `500` | **MAI의 개별 모델 요청** 제한(초); GAIA 문제 전체 한도와 다름 |

보호 `true`: MAI/OpenClaw → `127.0.0.1:8080` 게이트웨이 → `127.0.0.1:8081` Router. 보호 `false`: Router가 `127.0.0.1:8080`에 직접 바인딩. **두 경우 모두 외부 클라이언트는 8080을 사용한다.** 8081 직접 호출은 보호 계층을 우회하므로 비교 실험에 사용하지 않는다.

현재 구현은 새 요청의 RAM 검사와 로딩 전 VRAM 추정치를 확인하며, 추론 중 RAM을 기록하지만 실행 중 하드웨어 메모리 할당량을 강제 제한하거나 추론을 강제 중단하지는 않는다. 사용량 로그는 `mai/mcp_tools/mcp_memorizer/MAI_MEMORY/gpu_ram_safety.jsonl`이다. 비교 실험에서는 MAI·OpenClaw 모두 보호 기능의 ON/OFF 및 최소 RAM·VRAM 값을 동일하게 맞춘다.

### Portable Windows Router 런처 스위치

`portable/start-portable-llamacpp-router.ps1`의 인자는 다음과 같다.

| 스위치 | 기본값 | 동작 |
|---|---|---|
| `-ModelsMax` | `1` | 동시에 적재 가능한 모델 수; 현재 **1만 허용** |
| `-GpuLayers` | `99` | 모델 GPU 오프로딩 레이어 수 |
| `-ContextSize` | `0` | `0`이면 `settings.yaml` 값을 사용; 직접 설정 시 카탈로그에 반영 |
| `-GaiaBenchmark` | 꺼짐 | 격리된 MAI GAIA 평가 서버(8010)를 실행 |
| `-RouterOnly` | 꺼짐 | Router만 실행하여 OpenClaw 등의 클라이언트가 연결하도록 사용 |

`-GaiaBenchmark`와 `-RouterOnly`는 동시에 사용할 수 없다. GAIA MAI 평가에는 `start_benchmark_server.ps1`, OpenClaw 평가에는 `-RouterOnly`를 사용한다. 메모리 보호가 켜졌다면 `-RouterOnly`도 **Router와 공통 보호 게이트웨이**를 함께 실행한다.

직접 옵션을 확인하려면:

```powershell
& ".\benchmark\GAIA Benchmark\adapter\.venv\Scripts\python.exe" ".\benchmark\GAIA Benchmark\adapter\run_gaia.py" --help
& ".\benchmark\GAIA Benchmark\adapter\.venv\Scripts\python.exe" ".\benchmark\GAIA Benchmark\adapter\run_openclaw_gaia.py" --help
```

## 8. 공개 비교 시 주의

1. MAI vs Hermes vs OpenClaw는 동일 GGUF SHA-256, llama.cpp 빌드, ctx, GPU 오프로딩, 입력 문제, 실행 예산을 기록해야 한다.
2. 비교 대상별로 새 대화·작업 공간을 사용하고 이전 문제 정답·결과를 문맥에 넣지 않는다.
3. 첨부파일 문제는 **MAI 서버가 파일 경로에 직접 접근 가능한 동일 컴퓨터에서만** 실행한다. 비이미지 자료는 정답 메타데이터를 포함한 원본 데이터 폴더가 아닌 \`result/실행폴더/inputs/\`에 복사하고, MAI의 기존 파일 도구를 통해 읽도록 절대 경로를 문제 입력에 제공한다. 단, MAI 서버가 같은 OS 사용자 권한으로 실행되면 데이터 원본 접근 자체를 차단하지는 못한다. 정식 공정성 검증에는 평가 데이터가 접근 불가능한 격리 실행 계정 또는 별도 프로세스 권한 구성이 필요하다.
4. `run_gaia.py`는 질문에 `FINAL ANSWER: <answer>` 최종 줄을 요구한다. 마지막 줄이 그 형식이 아니면 실패로 기록한다. 첨부파일은 `result/실행폴더/inputs/<task_id>/`로 복사해 절대경로를 프롬프트에 제공하며, 별도의 이미지 업로드 API로 자동 첨부하지 않는다. 따라서 첨부 형식별 실제 접근 능력과 모델의 이미지 입력 능력을 혼동하지 않는다.
5. 점수에는 정답률 외에 문제당 시간, 모델 라운드 수, 도구 호출 수, 실패 및 타임아웃을 포함한다.
6. GAIA는 외부 웹 자료가 변경될 수 있는 문제들이 있다. 실행 날짜와 외부 접근 범위를 기록한다.
7. 실험에서 문제별 장기기억 데이터가 DB 경로로 격리되어도, **동일 실행 중** 문제 간 기억 간섭 가능성이 있으므로 최종 공식 시험에서는 독립 실행/기억 리셋 정책을 추가 검증해야 한다.
8. GAIA 질문 및 첨부파일은 사용자 접근 승인을 받은 후에만 다운로드하고, 공개 PR이나 버그 보고에 포함하지 않는다.

## 9. 고장 진단

- 모든 문제가 수 초 만에 `All connection attempts failed`로 끝나면 먼저 `127.0.0.1:8080/v1/models`와 시험 서버 `/health`를 각각 확인한다. **기존 문서에는 router를 시작하는 단계가 없었다. 지금은 `start_benchmark_server.ps1` 한 번으로 두 서비스를 실행하므로 일반 `./mai.sh start`를 추가로 실행하지 않는다.** `8010`의 정상 구동만으로 추론 서버 연결이 증명되지는 않는다.
- llama.cpp 모델 호출 `ModelChatTimeoutError`와 평가 실행기의 `--timeout-seconds` 초과는 다른 제한이다. 모델 요청 제한은 현재 `settings.yaml`의 `MODEL_REQUEST_TIMEOUT_SECONDS: 500`이며 평가 실행기는 기본 1800초다.
- 검색 반복, 외부 사이트 차단, 답변 형식 위반은 `execution.jsonl` 및 시험 서버의 감사 로그(`result/_runtime/log.sqlite3`)를 함께 대조한다. 실패를 정답 오답과 혼동하지 않는다.
- `--output-dir`을 변경하지 않고 재개하면 이미 `predictions.jsonl`에 기록된 실패 건도 건너뛴다. 재시험은 새로운 결과 폴더에서 수행한다.

## 10. 하드웨어 기준

i5-12400F, RX 6800 XT 16GB의 Ornith 9B 단일 실행 구성을 예시로 사용한다. 실제 호스트 RAM 용량은 실행 기기에 따라 달라지므로 실행 전 확인한다. GAIA 자체는 GPU 계산을 수행하지 않으며, 에이전트별 **순차 실행**을 권장한다. 현재 설정의 64K 컨텍스트에서도 RAM/VRAM 부담이 생길 수 있으므로 첫 10문제에서 피크 메모리를 확인한다.
