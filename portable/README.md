# MAI Portable — 준비·실행·백업

Windows/Linux 호스트 OS 인터페이스 구조와 이식성 제한은 [OS 경계 문서](../docs/platform-boundary.md)를 참조한다.

외장 SSD에 소스·설정·DB·모델을 두고 Windows x64에서 실행한다. Ubuntu 24.04 x86-64는 실험적 지원이며 실행 환경을 호스트 캐시에 별도로 준비한다. 일반 설치·계정·음성·Tailscale 설정은 [설치 안내](../setup.md)를 따른다.

원격 웹 공개 제공자 선택은 [한국어 원격 호스팅 설정](../docs/remote-hosting.md)을 참조한다.

## 짧은 실행 명령 (Windows)

아래 명령어는 모두 **프로젝트 루트**에서 실행한다. 기존 `portable/` 설치·실행 스크립트의 동작은 그대로 유지한다.

| 작업 | Git Bash | CMD | PowerShell |
|---|---|---|---|
| llama.cpp MAI 실행 | `./mai.sh start` | `mai.cmd start` | `.\\mai.ps1 start` |
| Portable 런타임 설치·준비 | `./mai.sh setup` | `mai.cmd setup` | `.\\mai.ps1 setup` |
| 준비 상태 점검 | `./mai.sh check` | `mai.cmd check` | `.\\mai.ps1 check` |

`setup`은 `portable/portable-sources.json`을 자동으로 찾아 설치 스크립트의 필수 `-Manifest` 인자로 전달한다. Manifest가 없으면 **오류를 출력하고 종료**한다. 최초 Manifest 생성은 기존과 같이 `python portable/resolve_portable_sources.py --include-llamacpp` 명령으로 수행하며, 공식 SHA-256 검증은 유지한다. `setup`은 아직 기존 Windows 설치 스크립트를 사용하므로 Ollama 런타임 설치 의존성도 유지된다.

`check`는 Python 및 llama.cpp 실행 파일, `.env`, `settings.yaml`, 현재 모델 카탈로그를 검사하지만 실제 서버 실행이나 GPU 메모리 사용량을 검증하지 않는다. `start`는 `check` 후 llama.cpp 라우터를 시작한다. 설정은 계속 `settings.yaml`과 `.env`에서 읽는다. 모델이 없거나 설정이 잘못된 경우 대체 모델로 전환하지 않는다.

루트의 `mai/`가 이미 Python 패키지 디렉터리라서 동명 `./mai` 파일을 만들 수 없다. Git Bash 실행 파일은 `./mai.sh`로 제공한다. Linux Portable은 기존 `portable/start-portable-llamacpp-linux.sh`를 그대로 이용하며 이 Windows 전용 래퍼는 실행하지 않는다.

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
| `-Models "ornith-1.5:9b,lfm3-vl:2b,LiquidAI/lfm2.5-2.6b:q4_k_m"` | 위 옵션과 함께 지정한 모델의 매니페스트·참조 blob만 검증 후 복사 |
| `-PullModels` | 매니페스트 목록과 현재 메인·비전·verifier 모델을 중복 없이 다운로드 |
| `-InstallChromium` | Portable Playwright Chromium 설치 |
| `-PreserveAndReinstallExisting` | 설치 영수증이 없는 기존 런타임을 별도 보존한 뒤 재설치 |

`-Models`는 `-CopyLocalModels`와만 사용하며 `-PullModels`와 조합하지 않는다. 전체 복사는 기존 대상이 있으면 실패한다. 선택 복사는 일치하는 기존 파일을 검증 후 재사용하지만 손상되거나 다른 매니페스트는 덮어쓰지 않는다. 공유 blob은 한 번만 복사한다.

런타임 재실행은 URL·해시·실행 파일이 일치하는 영수증이 있을 때 재사용한다. 다른 출처나 영수증 없는 설치본을 조용히 덮어쓰지 않는다. 보존 후 재설치는 `.before-portable-...` 경로에 이전 런타임을 남기며, 이미 검증된 환경을 강제로 재설치하는 옵션은 아니다.

새 모델을 내려받는 경우의 예:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/prepare-portable.ps1 -Manifest ./portable/portable-sources.json -PullModels -InstallChromium
```

버전 관리되는 Windows 매니페스트 예시에는 `ornith-1.5:9b`, `lfm3-vl:2b`, `LiquidAI/lfm2.5-2.6b:q4_k_m`가 포함된다. `-PullModels`는 기존 매니페스트를 그대로 사용해도 현재 설정의 `MAIN_MODEL`, `VISION_MODEL`, `VERIFIER_MODEL`을 추가한다. 설정 우선순위는 MAI와 동일하게 프로세스 환경변수 → 프로젝트 `.env` → `settings.yaml`이다. 매니페스트에 별도로 지정한 모델도 유지하며, 각 모델은 다운로드 후 `ollama show`로 확인한다. 런타임 다운로드 URL·해시를 다시 생성할 필요는 없다. 예전 개인 `portable-sources.json`에 `lfm2.5:8b`가 남아 있으면 `-PullModels`는 그 모델도 다운로드한다. 더 이상 필요 없다면 매니페스트의 `models` 목록에서 직접 제거한다. 기존 `.env` 또는 프로세스 환경변수의 `VERIFIER_MODEL`도 `settings.yaml`보다 우선하므로 확인한다.

### 기존 외장하드에 Verifier 모델만 추가 또는 변경

Verifier를 기존 모델에서 `LiquidAI/lfm2.5-2.6b:q4_k_m`로 변경할 때에도 기존 런타임·DB·모델을 삭제하지 않는다. PC의 일반 Ollama에 `LiquidAI/lfm2.5-2.6b:q4_k_m`가 이미 있으면 다시 다운로드하지 않는다. 없다면 일반 Ollama를 실행한 상태에서 `ollama pull LiquidAI/lfm2.5-2.6b:q4_k_m`를 먼저 실행한다. 다운로드가 끝나면 MAI·Ollama 및 모델 쓰기 작업을 종료한다.

PowerShell에서 **외장하드의 MAI 프로젝트 루트**로 이동해 다음을 실행한다. `OLLAMA_MODELS`를 사용자 지정했다면 아래 원본 경로가 실제 일반 Ollama 서버의 저장 위치인지 확인한다.

```powershell
$sourceModels = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { Join-Path $env:USERPROFILE '.ollama\models' }
& ./portable/portable-runtimes/python/python.exe ./portable/copy_ollama_models.py --source "$sourceModels" --destination ./portable/portable-models/ollama --model LiquidAI/lfm2.5-2.6b:q4_k_m
if ($LASTEXITCODE -ne 0) { throw 'Verifier 모델 복사 실패' }
```

해당 모델의 매니페스트와 참조 blob만 SHA-256·크기를 검증해 추가한다. 다른 모델은 그대로 두며, 이미 일치하는 파일은 재사용한다. 복사 후 `start-portable.ps1`로 실행하고 별도 PowerShell 창에서 `./portable/portable-runtimes/ollama/ollama.exe show LiquidAI/lfm2.5-2.6b:q4_k_m`로 확인한다.

`--include-llamacpp`는 목록 생성기 옵션이고 PowerShell 설치기 옵션이 아니다. GGUF 모델은 별도로 준비한다. 설치 준비는 실제 GPU·음성·새 PC의 완전 오프라인 실행을 보장하지 않는다.


기본 `MAI_ANSWER_QUALITY_REVIEW=false`에서는 선택적 품질 검토 호출이 실행되지 않는다. 숫자검증과 사실검증은 독립적으로 실행되며 같은 Verifier 모델을 사용한다. 기존 `.env` 또는 환경변수에 `MAI_ANSWER_QUALITY_REVIEW=false`가 있으면 새 기본값보다 우선한다.

## 원격 웹 호스팅과 원격 PC 작업

Portable 실행 스크립트는 MAI 서버 자체를 시작한다. 웹의 외부 공개 방법은 **호스트 OS에 설치된 커넥터**가 담당하며 Portable에 Tailscale이나 cloudflared 로그인 상태까지 자동 포함되지 않는다.

```yaml
# settings.yaml (기본값)
REMOTE_HOSTING_PROVIDER: tailscale  # tailscale | cloudflare | none
```

- **Tailscale(기본):** Windows/Linux 호스트에 Tailscale을 설치하고 로그인한 뒤 Funnel을 허용한다. `tailscale funnel status`로 공개 상태를 확인한다. 설치·로그인하지 않은 PC에서는 커넥터 시작이 실패할 수 있으나 로컬 MAI는 계속 실행한다.
- **Cloudflare:** 호스트에 `cloudflared`를 설치하고 PATH에 등록한다. Cloudflare Dashboard에서 관리형 Named Tunnel과 공개 호스트명 → `http://127.0.0.1:8000` 경로를 만들고, Git 제외 `.env`에 `CLOUDFLARE_TUNNEL_TOKEN=실제토큰`을 입력한다.
- **none:** 이번 MAI 프로세스에서 원격 호스팅 커넥터를 새로 실행하지 않는다.
- **기존 설정 정리:** 구형 `TAILSCALE_FUNNEL`은 더 이상 사용하지 않는다. 이전 `.env` 파일에서 제거할 수 있다. `REMOTE_HOSTING_PROVIDER`가 `.env`에 있으면 YAML보다 우선한다.
- **제공자 전환:** 기존 Tailscale Funnel은 백그라운드에서 유지될 수 있으므로 `tailscale funnel status` 확인 후 불필요한 공개 규칙을 직접 해제한다. `none`으로 전환해도 이전에 다른 프로세스가 열어 둔 공개 경로까지 자동으로 닫지는 않는다.
- **원격 PC 관리:** MAI 웹 호스팅과 별도의 `mai/remote/` 기능이다. `dev` 원격 PC 세션은 연결 시도 10분, 연결 확인 3분 및 **실제 원격 작업 없이 10시간 경과하면 자동 만료** 규칙을 갖는다. 이 제한은 웹 터널을 끄지 않는다.

Tailscale/Cloudflare 최초 설정과 전환 방법은 [원격 호스팅 가이드](../docs/remote-hosting.md)를 확인한다. MAI 웹 로그인에 강한 비밀번호를 설정하고, `TRIAL_USERS`의 권한을 점검한 뒤 외부 접속을 테스트한다.

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

## Linux Vulkan GPU (AMD / Intel / NVIDIA)

Ubuntu 24.04 호스트의 GPU 드라이버는 외장 SSD의 Windows 실행 파일과 별개로 설치해야 한다. AMD RX 6800 XT는 Linux `amdgpu` 커널 드라이버와 Mesa RADV Vulkan 경로를 사용할 수 있지만, 해당 호스트에서 실제로 작동하는지 확인해야 한다.

```bash
python3 portable/check-linux-gpu.py --require
```

AMD·Intel 호스트에 Vulkan 런타임 패키지를 설치하려면 관리자 권한과 네트워크 접속이 필요하다.

```bash
bash portable/prepare-portable-linux.sh --install-vulkan
```

NVIDIA는 Mesa RADV 대신 GPU·커널에 맞는 NVIDIA Vulkan 드라이버가 필요하며, 이 준비 명령이 NVIDIA 드라이버를 자동 설치하지는 않는다. `vulkaninfo`가 소프트웨어 장치만 찾으면 사전 점검은 실패한다. 점검 통과는 GPU 탐지를 의미하며, MAI의 모델 레이어가 실제로 GPU에 올라갔다는 증거는 아니다. 추론 로그와 GPU 사용량을 별도로 확인한다.

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

최초 패키지 설치는 인터넷이 추가로 필요할 수 있다. 시스템 라이브러리·GPU 드라이버는 자동 설치하지 않는다. Ubuntu에서는 `bash portable/prepare-portable-linux.sh --install-vulkan`으로 Mesa Vulkan 패키지를 명시적으로 설치할 수 있으며, `python3 portable/check-linux-gpu.py --require`로 하드웨어 Vulkan 장치를 확인한다. 실제 모델 GPU 추론은 별도 검증이 필요하다. 실제 Ubuntu 종단간 실행과 새 PC의 완전 오프라인 최초 실행은 아직 보장되지 않는다. Tailscale도 호스트별 설치·인증이 필요하다.

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

Verifier 기본 태그는 `LiquidAI/lfm2.5-2.6b:q4_k_m`이며, 일반 Ollama에서 `ollama pull LiquidAI/lfm2.5-2.6b:q4_k_m`로 먼저 다운로드할 수 있다. 외장 SSD에는 `-CopyLocalModels -Models "LiquidAI/lfm2.5-2.6b:q4_k_m"` 또는 `-PullModels`로 추가한다. 이전 개인 매니페스트에 남은 모델은 자동 삭제하지 않는다.


## llama.cpp 라우터 — 선택형 실행 경로

기본 백엔드는 Ollama이며 llama.cpp는 명시적으로 선택할 때만 사용한다. 기존 `start-portable.ps1`의 Ollama 동작을 변경하지 않는다. 라우터는 `--models-max 1`로 동시에 하나의 모델만 적재하며, MAI가 메인·Verifier·기억 추출에 필요한 모델 호출 전에 언로드/로드 완료 상태를 확인한다. Main이 임시답변을 생성한 뒤 Verifier로 전환할 때 Main KV는 보존하지 않는다. 교정 때에는 MAI의 기존 메시지·도구 결과와 최신 반려 초안·피드백을 다시 구성해 전달한다. 별도 KV 저장/복원 기능은 없다.

### Windows 준비·실행

```powershell
python portable/resolve_portable_sources.py --include-llamacpp
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/prepare-portable.ps1 -Manifest ./portable/portable-sources.json
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/start-portable-llamacpp-router.ps1
```

매니페스트가 이미 있으면 생성 명령은 기존 파일을 임의로 덮어쓰지 않는다. 런타임 URL·SHA-256을 검토한 뒤 준비한다. GGUF·projector는 자동 다운로드되지 않으며, 라이선스와 원본 해시를 확인한 파일을 `portable/portable-models/gguf/`에 넣는다.

`settings.yaml`의 현재 주요 설정은 다음과 같다.

```yaml
LLAMACPP_GGUF_DIRECTORY: "portable/portable-models/gguf"
LLAMACPP_MAIN_GGUF_PATH: "portable/portable-models/gguf/Ornith-1.5-9B-Q4_K_M.gguf"
LLAMACPP_MAIN_MMPROJ_PATH: "portable/portable-models/gguf/mmproj-Ornith-1.5-9B-BF16.gguf"
LLAMACPP_MAIN_VISION: true
LLAMACPP_REASONING_PRESERVE: false
```

모델 카탈로그는 지정 폴더의 **직접 자식 GGUF 파일**을 시작 시 검증해 생성한다. `LLAMACPP_MAIN_GGUF_PATH`는 기본 선택값이다. 옛 `mai-main`/`mai-verifier` 별칭과 별도의 `LLAMACPP_VERIFIER_GGUF_PATH` 입력을 필수로 요구하는 설치 설명은 현재의 UI 모델 선택 계약이 아니다. 현재 llama.cpp 라우터에서는 선택된 모델을 Main·Verifier·기억 추출에 공통 사용하며 검증 단계별 프롬프트·응답 스키마는 분리된다. 모델 변경 시 실제 언로드/로드 완료를 확인한다. 목록 변경은 MAI 재시작이 필요하다. 세부 사항은 [GGUF 선택](../docs/llamacpp-gguf-model-selection.md)을 따른다.

### Ubuntu 24.04 x86-64 준비·실행

```bash
python3 portable/resolve_portable_sources_linux.py --include-llamacpp
bash portable/prepare-portable-linux.sh --with-llamacpp
bash portable/start-portable-llamacpp-linux.sh
```

AMD/Intel Linux에서 필요한 Vulkan 패키지를 설치할 때에는 별도로 `--install-vulkan` 옵션을 사용한다. Linux의 Python·llama.cpp 네이티브 실행 환경은 프로젝트 경로별 호스트 캐시 아래 준비된다. Windows `.exe`를 Linux에서 실행하지 않는다. 실행 드라이브의 권한·용량·GPU/Vulkan 지원을 실기기에서 확인한다.

Linux 원격 **대상** 워커도 별도 Linux shell 시작 스크립트를 제공한다. 이는 llama.cpp Linux Portable과 별개의 기능이며, 설치·인증·실제 원격 도구 동작은 [원격 워커 문서](../docs/remote-native-worker.md)를 따라 검증한다.

### 회귀 테스트와 실기기 확인

```bash
python -m pytest -q tests/test_llamacpp_auto_catalog_and_switch.py tests/test_llamacpp_shared_model_alias.py tests/test_linux_llamacpp_portable.py tests/test_llamacpp_confirmed_unload.py tests/test_final_grounding_verifier.py
python -m pytest -q
```

테스트 결과 외에 Main 도구 호출, Verifier 구조화 JSON, 이미지 입력(지원 모델·mmproj 설정 시), 기억 추출, 모델 전환 후 VRAM 사용 및 실제 OS별 실행을 검증해야 한다. 모델 등록과 GGUF 헤더 검증은 정상 추론을 보장하지 않는다.

### llama.cpp 모드에서 Ollama 서버 없이 실행

llama.cpp 라우터는 독립 로컬 HTTP 서버와 `LlamaCppAdapter`를 사용하므로 실행 중인 Ollama 데몬이나 Ollama 모델 저장소가 필요하지 않다. Ollama SDK는 기본 프로젝트 의존성으로 선언되어 있으며 **일부 테스트는 직접 `ollama.ResponseError`를 import한다**. 따라서 llama.cpp 런타임에서 Ollama 서버가 불필요하다는 사실을 전체 테스트 환경에서 `ollama` Python 패키지도 생략해도 된다는 뜻으로 해석해서는 안 된다.

Portable Python에서 필수 패키지가 누락되면 원래의 `prepare-portable.ps1` 준비 절차로 의존성을 맞춘다. llama.cpp만 실행하려고 Ollama 애플리케이션을 추가로 시작할 필요는 없다.

Portable Python에서 수행할 회귀 테스트:
```powershell
./portable/portable-runtimes/python/python.exe -m pytest -q tests/test_optional_ollama_dependency.py tests/test_llamacpp_adapter.py tests/test_ollama_adapter.py tests/test_final_grounding_verifier.py
```


### llama.cpp 모델 전환 확인 (Main ↔ Verifier)

`--models-max 1` 제한에 더하여 MAI의 llama.cpp 어댑터는 **다른 GGUF를 호출하기 직전** `GET /models`로 현재 상태를 확인하고, 기존 활성 모델에 `POST /models/unload`를 요청한 뒤 `GET /models`에서 해당 모델의 상태가 명확히 `unloaded`가 될 때까지 기다린다. 언로드 확인 제한은 기본 30초, 새 모델 로드 확인 제한은 기본 180초다. 언로드 요청 실패·응답 형식 오류·완료 확인 실패 시 **다음 모델을 호출하지 않고 오류를 반환**한다. 같은 모델을 연속 호출할 때는 언로드하지 않으며, 따라서 메인 모델의 도구 턴 중 KV 재사용 기회가 유지된다.

이 동작은 메인, Verifier, 기억 추출, 선택형 Vision 도구가 동일하게 사용한다. Verifier 이후 메인을 호출하는 경우에도 역방향 확인을 수행한다. **GPU 메모리 사용량이나 드라이버의 메모리 반환 상태를 별도 측정하지 않는다.** 상태 `unloaded` 확인은 라우터가 보고하는 언로드 완료이며, 시스템 전체의 VRAM 상태를 보증하지 않는다. 다른 프로세스의 GPU 사용량도 통제하지 않는다.

테스트: `python -m pytest -q tests/test_llamacpp_confirmed_unload.py tests/test_llamacpp_adapter.py tests/test_rejected_draft_failure_display.py`. 실제 Windows/Linux llama.cpp 빌드에서 `/models` 상태 전환을 확인하기 전까지는 실기기 검증 완료로 취급하지 않는다.


### 이미지 입력 및 Main KV 유지 (llama.cpp Portable)

llama.cpp 모드는 **메인 모델에만 이미지 바이트를 직접 전달**한다. `LLAMACPP_MAIN_VISION=true`(모델과 mmproj가 실제로 지원되는 경우)에만 멀티모달 이미지 메시지를 보낸다. 별도의 `image_analyze` 도구는 llama.cpp 모드에서 **등록하지 않는다**. 이로써 이미지 분석을 위해 메인 모델이 중간에 Verifier/전용 Vision 모델로 교체되는 상황을 방지한다.

이미지 첨부가 있지만 `LLAMACPP_MAIN_VISION=false`라면 서버 오류나 다른 모델로의 우회 대신, **메인 모델에 이미지 바이트가 전달되지 않았음을 알리고** 사용자에게 현재 모델에서는 이미지를 해석할 수 없다고 설명하도록 요청한다. 이미지를 보지 않았는데 본 것처럼 내용을 추측해서는 안 된다. 단, 이 동작은 모델에 대한 메시지 지시이지 실제 출력의 절대적인 보장은 아니다. 모델이 비전 지원을 주장하더라도 실제 GGUF·mmproj·llama.cpp의 호환성은 실기기 테스트로 확인해야 한다.

실행 순서: **Main 반복 도구 턴 → 임시답변 → Main 언로드 상태 확인 → Verifier 적재 및 검증 → REJECT 시 Verifier 언로드 상태 확인 → Main 재적재 → 기존 문맥 + 최신 임시답변(미검증 초안) + 거절 피드백으로 다음 턴**. 본문·도구 결과는 MAI 로그/대화 기록으로 다시 제공하며, 모델 언로드 후의 KV는 재사용하지 않는다. ACCEPT 시에도 검증이 완료되기 전에는 최종답변을 공개하지 않는다.

Ollama 백엔드의 기존 `image_analyze` 별도 모델 경로는 변경하지 않는다.


**Verifier HTTP 500 진단:** `/models/load` 실패 시 HTTP 코드·응답 본문(최대 800자), `/v1/chat/completions` 실패 시 HTTP 코드·모델 별칭·응답 본문(최대 1,000자)을 포함한 오류를 반환한다. 컨텍스트/VRAM/모델 GGUF 버전/라우터 서버 자체의 실패와 단순 JSON 파싱 오류를 혼동하지 않는다. 실기기 검증 전까지는 자동 재시도나 실패 응답 대체를 하지 않는다.

### llama.cpp 128K 기본 컨텍스트 (Windows / Ubuntu)

Windows `start-portable-llamacpp-router.ps1`의 기본 `-ContextSize`는 131072이며, Linux `start-portable-llamacpp-linux.sh`도 모델 프리셋에 `ctx-size = 131072`를 작성한다. 단일 모델 상주와 명시적 언로드/로드 확인 계약은 유지한다.

**주의:** 128K 설정은 모델의 학습된 문맥 길이, GPU에서의 실제 할당 성공 또는 해당 길이의 안정적인 추론을 보장하지 않는다. 가중치·KV·projector·드라이버가 메모리를 함께 사용하므로 16GB VRAM에서는 할당 실패 또는 CPU 오프로딩이 발생할 수 있다. 불안정하면 중단해 확인한다. Windows는 실행 시 `-ContextSize 32768` 또는 `-ContextSize 16384`로 줄일 수 있고, Linux는 실행 스크립트의 `ctx-size` 기본값 변경이 필요하다.

웹 도구 결과는 원문 저장소와 감사 로그에 보존하고 이후 Main 차례에는 출처 기반 발췌·URL·원문 참조 ID를 전달한다. [도구 결과 전달](../docs/TOOL_RESULT_PAGINATION.md) 참고. 반복 문맥을 줄이지만 128K 안정성이나 완전한 KV 재사용을 보장하지 않는다.
