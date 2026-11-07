# MAI Portable — 준비·실행·백업

외장 SSD에 소스·설정·DB·모델을 두고 Windows x64에서 실행한다. Ubuntu 24.04 x86-64는 실험적 지원이며 실행 환경을 호스트 캐시에 별도로 준비한다. 일반 설치·계정·음성·Tailscale 설정은 [설치 안내](../setup.md)를 따른다.

## 배치와 준비 조건

다음 명령은 모두 **프로젝트 루트**에서 실행한다. SSD는 NTFS를 사용하며 포맷 전 별도 디스크에 백업한다. 포맷은 데이터를 삭제한다. 호스트 GPU 드라이버·시스템 라이브러리·Tailscale 인증은 SSD만으로 제공되지 않는다.

```text
MAI/
  .env                         # 개인 계정·경로, Git 제외
  settings.yaml
  mai/
  run_server.py
  data/                        # 공통 기억·대화·감사 DB
  mai_uploads/
  portable/
    README.md
    prepare-portable.ps1
    start-portable.ps1
    prepare-portable-linux.sh
    start-portable-linux.sh
    resolve_portable_sources.py
    resolve_portable_sources_linux.py
    copy_ollama_models.py
    model_requirements.py
    portable_backup.py
    verify_portable_backup.py
    portable-sources.example.json
    portable-sources.json       # Windows 배포 URL·해시, Git 제외
    portable-sources-linux.json # Linux 배포 URL·해시, Git 제외
    portable-downloads/        # 원본 아카이브, Git 제외
    portable-runtimes/         # Windows 실행 환경, Git 제외
    portable-models/           # ollama/ 및 선택적 gguf/, Git 제외
```

기존 루트의 `portable-*` 폴더와 파일을 위 위치로 모았다. 다른 설치본을 갱신할 때는 MAI·Ollama·준비 작업을 종료하고 기존 다운로드·런타임·모델 및 개인 매니페스트를 `portable/`로 이동한다. 기존 `.env`의 사용자 지정 경로도 확인한다. 구 경로를 자동 탐색하거나 새로 다운로드하는 대체 동작은 없다.

## Windows 최초 준비

Git·Python·PowerShell과 최초 다운로드용 인터넷이 필요하다. 저장소를 SSD에 준비하고 `.env.example`을 `.env`로 복사해 계정을 설정한다. 시스템의 가상환경을 옮기는 대신 이동 가능한 Python을 새로 준비한다.

### 배포 URL과 해시 고정

```powershell
python portable/resolve_portable_sources.py
```

공식 GitHub 릴리스의 URL과 **배포자가 제공한 SHA-256**을 `portable/portable-sources.json`에 기록한다. llama.cpp Windows Vulkan까지 준비하려면 `--include-llamacpp`를 추가한다. 최신 릴리스에 해당 바이너리가 없으면 최근 릴리스 목록에서 적합한 자산을 찾는다. 파일·배포자 해시가 없거나 기존 출력 파일이 있으면 실패한다.

직접 고정하려면 `portable-sources.example.json`을 복사해 정확한 HTTPS URL·해시를 채운다. Python은 Windows x64 `python-build-standalone`의 `install_only` 아카이브, uv는 x64 ZIP, Ollama는 독립 CLI ZIP을 사용한다. CPython 임베디드 ZIP이나 `ollama.exe` 단독 복사로 전체 환경이 갖춰진다고 가정하지 않는다.

GitHub 비인증 호출 제한에 걸리면 실패한다. 필요 시 `GITHUB_TOKEN`을 사용자 환경변수로 제공하되 토큰을 명령 인수·문서·커밋에 기록하지 않는다. 다운로드 뒤 계산한 해시를 독립적인 배포자 인증처럼 사용하지 않는다.

### 실행 환경과 모델 설치

기존 Ollama 쓰기 작업을 종료하고 실행한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/prepare-portable.ps1 -Manifest ./portable/portable-sources.json -CopyLocalModels -InstallChromium
```

| 옵션 | 의미 |
|---|---|
| `-CopyLocalModels` | `OLLAMA_MODELS` 또는 사용자 `.ollama/models` 전체 복사 |
| `-Models "ornith-1.5:9b,lfm3-vl:2b,qwen3:4b-instruct-2507-q4_K_M"` | 위 옵션과 함께 지정한 모델의 매니페스트·참조 blob만 검증 후 복사 |
| `-PullModels` | 매니페스트 목록과 현재 메인·비전·verifier 모델을 중복 없이 다운로드 |
| `-InstallChromium` | Portable Playwright Chromium 설치 |
| `-PreserveAndReinstallExisting` | 설치 영수증이 없는 기존 런타임을 별도 보존한 뒤 재설치 |

`-Models`는 `-CopyLocalModels`와만 사용하며 `-PullModels`와 조합하지 않는다. 전체 복사는 기존 대상이 있으면 실패한다. 선택 복사는 일치하는 기존 파일을 검증 후 재사용하지만 손상되거나 다른 매니페스트는 덮어쓰지 않는다. 공유 blob은 한 번만 복사한다.

런타임 재실행은 URL·해시·실행 파일이 일치하는 영수증이 있을 때 재사용한다. 다른 출처나 영수증 없는 설치본을 조용히 덮어쓰지 않는다. 보존 후 재설치는 `.before-portable-...` 경로에 이전 런타임을 남기며, 이미 검증된 환경을 강제로 재설치하는 옵션은 아니다.

새 모델을 내려받는 경우의 예:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/prepare-portable.ps1 -Manifest ./portable/portable-sources.json -PullModels -InstallChromium
```

버전 관리되는 Windows 매니페스트 예시에는 `ornith-1.5:9b`, `lfm3-vl:2b`, `qwen3:4b-instruct-2507-q4_K_M`가 포함된다. `-PullModels`는 기존 매니페스트를 그대로 사용해도 현재 설정의 `MAIN_MODEL`, `VISION_MODEL`, `VERIFIER_MODEL`을 추가한다. 설정 우선순위는 MAI와 동일하게 프로세스 환경변수 → 프로젝트 `.env` → `settings.yaml`이다. 매니페스트에 별도로 지정한 모델도 유지하며, 각 모델은 다운로드 후 `ollama show`로 확인한다. 런타임 다운로드 URL·해시를 다시 생성할 필요는 없다. 예전 개인 `portable-sources.json`에 `lfm2.5:8b`가 남아 있으면 `-PullModels`는 그 모델도 다운로드한다. 더 이상 필요 없다면 매니페스트의 `models` 목록에서 직접 제거한다. 기존 `.env` 또는 프로세스 환경변수의 `VERIFIER_MODEL`도 `settings.yaml`보다 우선하므로 확인한다.

### 기존 외장하드에 Verifier 모델만 추가 또는 변경

Verifier를 기존 모델에서 `qwen3:4b-instruct-2507-q4_K_M`로 변경할 때에도 기존 런타임·DB·모델을 삭제하지 않는다. PC의 일반 Ollama에 `qwen3:4b-instruct-2507-q4_K_M`가 이미 있으면 다시 다운로드하지 않는다. 없다면 일반 Ollama를 실행한 상태에서 `ollama pull qwen3:4b-instruct-2507-q4_K_M`를 먼저 실행한다. 다운로드가 끝나면 MAI·Ollama 및 모델 쓰기 작업을 종료한다.

PowerShell에서 **외장하드의 MAI 프로젝트 루트**로 이동해 다음을 실행한다. `OLLAMA_MODELS`를 사용자 지정했다면 아래 원본 경로가 실제 일반 Ollama 서버의 저장 위치인지 확인한다.

```powershell
$sourceModels = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { Join-Path $env:USERPROFILE '.ollama\models' }
& ./portable/portable-runtimes/python/python.exe ./portable/copy_ollama_models.py --source "$sourceModels" --destination ./portable/portable-models/ollama --model qwen3:4b-instruct-2507-q4_K_M
if ($LASTEXITCODE -ne 0) { throw 'Verifier 모델 복사 실패' }
```

해당 모델의 매니페스트와 참조 blob만 SHA-256·크기를 검증해 추가한다. 다른 모델은 그대로 두며, 이미 일치하는 파일은 재사용한다. 복사 후 `start-portable.ps1`로 실행하고 별도 PowerShell 창에서 `./portable/portable-runtimes/ollama/ollama.exe show qwen3:4b-instruct-2507-q4_K_M`로 확인한다.

`--include-llamacpp`는 목록 생성기 옵션이고 PowerShell 설치기 옵션이 아니다. GGUF 모델은 별도로 준비한다. 설치 준비는 실제 GPU·음성·새 PC의 완전 오프라인 실행을 보장하지 않는다.


기본 `MAI_ANSWER_QUALITY_REVIEW=false`에서는 선택적 품질 검토 호출이 실행되지 않는다. 숫자검증과 사실검증은 독립적으로 실행되며 같은 Verifier 모델을 사용한다. 기존 `.env` 또는 환경변수에 `MAI_ANSWER_QUALITY_REVIEW=false`가 있으면 새 기본값보다 우선한다.

## Windows 실행

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/start-portable.ps1 -Backend ollama
```

`portable/portable-runtimes/python/python.exe`, Ollama, 모델, 프로젝트 루트의 DB·업로드·설정을 사용한다. `http://127.0.0.1:8000`에 접속한다. Ollama `11434` 또는 llama.cpp `8080` 포트 충돌은 실패이며 기존 서버를 임의 재사용하지 않는다.

llama.cpp 예:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/start-portable.ps1 -Backend llamacpp -GgufPath ./portable/portable-models/gguf/model.gguf -ModelAlias local-model
```

[백엔드 호환성·GGUF·모델 보존](../docs/llamacpp-portability.md)을 확인한다. Portable Ollama를 실행한 상태에서 다른 창에서 모델 목록을 확인할 수 있다.

```powershell
./portable/portable-runtimes/ollama/ollama.exe list
```

## Ubuntu 24.04 x86-64 실행 — 실험적

Windows의 MAI와 Ollama를 종료하고 SSD를 안전하게 분리한 뒤 연결한다. NTFS는 읽기·쓰기로 마운트하며 두 OS가 같은 SQLite를 동시에 쓰지 않는다. `python3`, `curl`, `tar`, `zstd`, `git`, `sha256sum`, `flock`와 호스트 시스템 라이브러리가 필요하다.

먼저 인터넷이 되는 PC에서 Linux 전용 매니페스트를 생성한다. Windows Python에서도 생성할 수 있다.

```bash
python3 portable/resolve_portable_sources_linux.py
```

Ubuntu에서 프로젝트 루트로 이동해 준비·실행한다.

```bash
bash ./portable/prepare-portable-linux.sh --install-chromium
bash ./portable/start-portable-linux.sh
```

Linux 원본 아카이브는 SSD의 `portable/portable-downloads/linux/`, 실행 환경은 호스트의 `~/.cache/mai-portable/<프로젝트 경로 식별자>/` 아래에 둔다. NTFS에 Linux 가상환경을 설치하지 않는다. 모델·DB·설정은 SSD의 공통 파일을 쓴다. 프로젝트 경로 변경이나 캐시 삭제 후에는 다시 준비해야 할 수 있다.

최초 패키지 설치는 인터넷이 추가로 필요할 수 있다. 시스템 라이브러리·GPU 드라이버는 자동 설치하지 않는다. 실제 Ubuntu 종단간 실행과 새 PC의 완전 오프라인 최초 실행은 아직 보장되지 않는다. Tailscale도 호스트별 설치·인증이 필요하다.

## 백업과 복원

MAI와 모델 서버를 종료하고 **별도 물리 디스크**에 백업한다. 프로젝트 루트에서:

```powershell
./portable/portable-runtimes/python/python.exe portable/portable_backup.py --source . --destination X:\MAI-Backups
./portable/portable-runtimes/python/python.exe portable/verify_portable_backup.py X:\MAI-Backups\mai-날짜
```

실제 생성된 날짜 폴더를 사용한다. SQLite backup API로 DB를 복사하고 파일별 SHA-256을 기록한다. 검증기는 파일 집합과 해시를 확인한다. `.env`, 기억·대화·로그, 모델·다운로드가 포함되므로 백업 디스크 접근과 암호화를 관리한다. 백업 폴더는 원본과 별도 트리여야 한다.

복원은 검증한 전체 스냅샷을 원하는 위치에 복사한 뒤 `.env`, 모델 목록과 실행 경로를 확인한다. 새 PC의 모델 추론·기억 검색·대화 DB·웹 도구·음성·원격 접근은 실제로 시험한다. 공개 다운로드 주소의 영속성과 환경 재현은 별개이므로 원본 아카이브와 모델도 보존한다.

## 오류 진단

| 증상 | 확인 |
|---|---|
| `python`이 실행되지 않음 | Windows 실행 별칭 또는 실제 Python 경로 |
| Git Bash에서 PowerShell 스크립트 실행 실패 | `powershell -File ./portable/…ps1` 형식 |
| 아카이브 해시 불일치 | 공식 출처·고정 매니페스트·다운로드 파일, 자동 무시 금지 |
| 기존 런타임 영수증 없음 | 백업 후 명시적 보존·재설치 옵션 검토 |
| 포트 충돌 | 기존 Ollama·MAI 종료 여부 |
| 실행 파일·DLL·GPU 오류 | 호스트와 배포 아카이브의 호환성 |
| Linux 설치 실패 | Ubuntu 버전·아키텍처·호스트 도구·인터넷·시스템 라이브러리 |

## 메인 모델 이미지 직접 입력

사용자 업로드 이미지를 첨부한 채팅에서 Ollama의 메인 모델이 `vision` capability를 제공하면 원본 이미지 바이트를 모델 메시지에 직접 포함한다. 지원하지 않으면 기존 `VISION_MODEL`의 `image_analyze` 도구 경로를 유지한다. `VISION_MODEL`은 자동 제거하지 않는다. 첨부 이름은 계정별 업로드 디렉터리에서 검증하며 이미지만 직접 전달한다. llama.cpp 백엔드에서는 이 릴리스의 직접 이미지 전송 대신 기존 이미지 도구를 사용한다.

Verifier 기본 태그는 `qwen3:4b-instruct-2507-q4_K_M`이며, 일반 Ollama에서 `ollama pull qwen3:4b-instruct-2507-q4_K_M`로 먼저 다운로드할 수 있다. 외장 SSD에는 `-CopyLocalModels -Models "qwen3:4b-instruct-2507-q4_K_M"` 또는 `-PullModels`로 추가한다. 이전 개인 매니페스트에 남은 모델은 자동 삭제하지 않는다.
