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
4. 아래의 방법으로 **데이터 다운로드를 실행하는 PowerShell 프로세스**에 `HF_TOKEN`을 제공한다. 실제 토큰을 Git, 대화·스크린샷, 명령줄 인수, 일반 문서에 적지 않는다.

PowerShell에서 토큰을 화면에 표시하지 않고 입력하려면 다음을 사용한다.

```powershell
$secret = Read-Host "HF 읽기 토큰" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    $env:HF_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    Remove-Variable secret, pointer -ErrorAction SilentlyContinue
}
```

`HF_TOKEN`은 **현재 PowerShell 세션**과 그 자식 프로세스에서만 사용한다. 영구 환경변수나 MAI `.env`에 저장할 필요가 없다. 이미 Hugging Face CLI에 로그인되어 있다면 해당 인증을 사용해도 된다. 토큰은 학습 데이터 다운로드에만 필요하며, 이미 로컬에 내려받은 GAIA 데이터를 실행하거나 채점할 때 재입력할 필요는 없다.

데이터 다운로드에 성공한 뒤 현재 PowerShell에서만 토큰을 지우려면 `Remove-Item Env:HF_TOKEN`을 실행한다. 사용자의 Hugging Face 계정 및 토큰은 MAI 서버 로그인에 사용되지 않는다.

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

**일반 MAI(`./mai.sh start`)를 따로 실행하지 않는다.** GAIA 전용 스크립트가 기존 Portable 런처를 사용해 **llama.cpp router 한 개(8080)**를 시작하고, 정상 준비를 확인한 뒤 **GAIA 전용 MAI 웹 서버 한 개(8010)**를 실행한다. 일반 MAI 서버(8000)는 시작하지 않는다. 이 명령은 이전에 실행 중인 router(8080)가 있으면 중복 실행하지 않고 명시적으로 실패한다. 따라서 먼저 일반 MAI와 기존 router를 종료한다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\benchmark\GAIA Benchmark\docs\start_benchmark_server.ps1"
```

이 한 명령으로 시작되는 구성은 `llama-server` **1개**와 MAI 평가용 서버 프로세스 **1개**다. 모델 상주 제한 `--models-max 1`도 유지한다. 서버는 `http://127.0.0.1:8010`에 바인딩하며 채팅·메모리·로그·문장 분리 DB를 `result/_runtime/`에 분리한다. **음성 프로필(`VOICE_PROFILE_DIR`), 업로드 디렉터리, 파일·터미널·네트워크 도구의 OS 접근 권한까지 분리하는 스크립트는 아니다.** 원격 공개 호스팅을 끈다. 메인 모델 ID는 Portable 실행기와 같은 GGUF 카탈로그를 읽어 자동으로 설정하며, 기본 GGUF 파일이 없거나 손상된 경우 명확히 실패한다. 일반 MAI용 `.env`의 비밀번호는 변경하지 않는다.

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

## 7. OpenClaw와 Hermes Agent 비교용 소스 다운로드

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\benchmark\GAIA Benchmark\docs\download_agents.ps1"
```

`benchmark/agents/openclaw` 및 `benchmark/agents/hermes-agent`에 각 프로젝트의 공식 GitHub 저장소를 얕게 복제한다. **두 에이전트의 설치·온보딩·구동은 자동으로 하지 않는다.** 비교 평가에서 동일한 Ornith 9B와 llama.cpp 서버를 연결할 때 필요한 각 프로젝트별 설정은 별도로 수행해야 한다.

공식 자료:
- OpenClaw: https://docs.openclaw.ai/install
- Hermes Agent: https://hermes-agent.nousresearch.com/docs/getting-started/installation
- GAIA: https://huggingface.co/datasets/gaia-benchmark/GAIA
- GAIA 제출: https://huggingface.co/spaces/gaia-benchmark/leaderboard

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
- llama.cpp 모델 호출 `ModelChatTimeoutError`와 평가 실행기의 `--timeout-seconds` 초과는 다른 제한이다. 모델 요청 제한은 현재 `settings.yaml`의 `MODEL_REQUEST_TIMEOUT_SECONDS: 300`이며 평가 실행기는 기본 1800초다.
- 검색 반복, 외부 사이트 차단, 답변 형식 위반은 `execution.jsonl` 및 시험 서버의 감사 로그(`result/_runtime/log.sqlite3`)를 함께 대조한다. 실패를 정답 오답과 혼동하지 않는다.
- `--output-dir`을 변경하지 않고 재개하면 이미 `predictions.jsonl`에 기록된 실패 건도 건너뛴다. 재시험은 새로운 결과 폴더에서 수행한다.

## 10. 하드웨어 기준

i5-12400F, RAM 16GB, RX 6800 XT 16GB의 기존 Ornith 9B 단일 실행 구성을 사용한다. GAIA 자체는 GPU 계산을 수행하지 않으며, 에이전트별 **순차 실행**을 권장한다. 128K 컨텍스트를 전부 사용하는 상황은 RAM/VRAM 부담이 생길 수 있으므로 첫 10문제에서 피크 메모리를 확인한다.
