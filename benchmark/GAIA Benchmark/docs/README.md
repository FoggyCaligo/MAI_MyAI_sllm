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
   │  ├─ score_gaia.py
   │  └─ data/gaia/                 # 인증 후 데이터 자동 다운로드(추적 안 함)
   └─ result/                       # 실행 결과(추적 안 함)
```

## 1. GAIA 데이터 접근 동의

1. Hugging Face 로그인: https://huggingface.co/
2. GAIA 공식 페이지 https://huggingface.co/datasets/gaia-benchmark/GAIA 에서 **데이터 이용 약관**에 직접 동의한다.
3. HF CLI를 사용해 로그인(`hf auth login`)하거나 본인 PC의 사용자 환경변수 `HF_TOKEN`을 설정한다. 이 토큰과 인증 파일을 Git에 올리지 않는다.
4. 데이터셋 파일은 약관상 공개 미러나 일반 GitHub 저장소에 재배포하지 않는다.

**중요:** GAIA 원본 데이터/첨부파일은 이 PR에 포함할 수 없다. 실제 문제·첨부파일은 아래 설정 스크립트가 본인의 인증 상태를 사용해 `adapter/data/gaia/`로 다운로드한다. 정답은 채점 과정에만 사용하며 MAI에 전달하지 않는다.

2025년 10월부터 GAIA는 Parquet 메타데이터를 제공한다. 코드에서 `task_id`, `Question`, `Level`, `Final answer`, `file_path` 필드를 사용한다.

## 2. Windows 자동 설정

저장소 루트의 PowerShell에서:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\benchmark\GAIA Benchmark\docs\setup.ps1"
```

위 명령은 새 PowerShell 프로세스에만 실행 정책 `Bypass`를 적용하며 시스템 전체 정책을 변경하지 않는다.

스크립트는 `adapter/.venv`를 만들고 Python 패키지를 설치한 후 GAIA 데이터를 다운로드한다. 기존 MAI 모델 설치를 변경하지 않는다. 설치·인증 실패 시 명시적으로 중단한다.

Linux/WSL은 `sh "benchmark/GAIA Benchmark/docs/setup.sh"`로 실행한다.

데이터 다운로드에는 Hugging Face에 접근할 수 있는 인터넷 연결이 필요하며, 인증 전에는 자동으로 약관을 대신 동의하지 않는다.

## 3. MAI 벤치마크 전용 서버

**사용 중인 MAI 서버를 종료한 뒤** 기존 llama.cpp router를 실행한 상태에서 별도 PowerShell 창에 다음 명령을 실행한다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\benchmark\GAIA Benchmark\docs\start_benchmark_server.ps1"
```

이 서버는 `http://127.0.0.1:8010`에 바인딩하며 채팅·메모리·로그·문장 분리 DB를 `result/_runtime/`에 분리한다. 원격 공개 호스팅을 끈다.

로그인 계정은 MAI 서버의 기존 `OWNER_USERS` 환경 설정을 사용한다. 기존 사용자의 비밀번호를 벤치마크 명령 인수에 직접 넣지 않는다. **장기기억 데이터뿐 아니라 시험용 서버 프로세스 환경 및 브라우저 세션을 분리해야 오염 위험을 줄일 수 있다.**

평가 작업 중 실제 파일·인터넷·셸 명령을 실행할 수 있으므로, 개인 파일·Git 작업 디렉터리가 아니라 별도 Windows 사용자 또는 VM에서 실행하는 것이 더 안전하다. 이 스크립트는 DB 경로 분리만 수행하며 OS 권한을 제한하거나 파일 작업을 샌드박싱하지는 않는다.

## 4. GAIA Level 1에서 10문제 실행

새 PowerShell에서:

```powershell
$env:MAI_BENCH_USER = "본인의-MAI-로그인-ID"
& ".\benchmark\GAIA Benchmark\adapter\.venv\Scripts\python.exe" ".\benchmark\GAIA Benchmark\adapter\run_gaia.py" --server http://127.0.0.1:8010 --split validation --level level1 --limit 10
```

비밀번호는 기본적으로 터미널의 숨김 입력으로 요청한다. 요청당 최대 실행 시간은 기본 1800초이며, `--timeout-seconds`로 변경할 수 있다. MAI 자체의 모델 호출 제한은 별도 적용된다.

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

중단된 실행 재개:

```powershell
& ".\benchmark\GAIA Benchmark\adapter\.venv\Scripts\python.exe" ".\benchmark\GAIA Benchmark\adapter\run_gaia.py" --server http://127.0.0.1:8010 --split validation --level level1 --limit 10 --output-dir ".\benchmark\GAIA Benchmark\result\gaia_실행폴더"
```

같은 매개변수로 재개하면 완료된 `task_id`를 건너뛴다. **실패한 문제도 체크포인트에 기록하므로 재실행하려면 새로운 출력 폴더를 사용한다.** 실패를 성공으로 취급하지 않는다.

## 6. 간이 채점

```powershell
& ".\benchmark\GAIA Benchmark\adapter\.venv\Scripts\python.exe" ".\benchmark\GAIA Benchmark\adapter\score_gaia.py" ".\benchmark\GAIA Benchmark\result\gaia_실행폴더\predictions.jsonl" --level level1
```

이 채점은 공백 제거·대소문자 통일만 수행하는 **보수적 참고 지표**다. GAIA 공식 평가의 정규화 규칙과 동일하지 않으므로 공식 리더보드 점수로 발표하면 안 된다. 시험 문제의 정답이 공개되지 않은 `test`는 로컬 채점하지 않는다.

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
3. 첨부파일 문제는 **MAI 서버가 데이터 경로에 직접 접근 가능한 동일 컴퓨터에서만** 실행한다. 비이미지 자료는 정답 메타데이터를 포함한 원본 데이터 폴더가 아닌 \`result/실행폴더/inputs/\`에 복사하고, MAI의 기존 파일 도구를 통해 읽도록 절대 경로를 문제 입력에 제공한다. 단, MAI 서버가 같은 OS 사용자 권한으로 실행되면 데이터 원본 접근 자체를 차단하지는 못한다. 정식 공정성 검증에는 평가 데이터가 접근 불가능한 격리 실행 계정 또는 별도 프로세스 권한 구성이 필요하다.
4. 점수에는 정답률 외에 문제당 시간, 모델 라운드 수, 도구 호출 수, 실패 및 타임아웃을 포함한다.
5. GAIA는 외부 웹 자료가 변경될 수 있는 문제들이 있다. 실행 날짜와 외부 접근 범위를 기록한다.
6. 실험에서 문제별 장기기억 데이터가 DB 경로로 격리되어도, **동일 실행 중** 문제 간 기억 간섭 가능성이 있으므로 최종 공식 시험에서는 독립 실행/기억 리셋 정책을 추가 검증해야 한다.
7. GAIA 질문 및 첨부파일은 사용자 접근 승인을 받은 후에만 다운로드하고, 공개 PR이나 버그 보고에 포함하지 않는다.

## 9. 하드웨어 기준

i5-12400F, RAM 16GB, RX 6800 XT 16GB의 기존 Ornith 9B 단일 실행 구성을 사용한다. GAIA 자체는 GPU 계산을 수행하지 않으며, 에이전트별 **순차 실행**을 권장한다. 128K 컨텍스트를 전부 사용하는 상황은 RAM/VRAM 부담이 생길 수 있으므로 첫 10문제에서 피크 메모리를 확인한다.
