# MAI Portable — 준비·실행·백업

Windows/Linux 호스트 OS 인터페이스 구조와 이식성 제한은 [OS 경계 문서](../docs/platform-boundary.md)를 참조한다.

외장 SSD에 소스·설정·DB·모델을 두고 Windows x64에서 실행한다. Ubuntu 24.04 x86-64는 실험적 지원이며 실행 환경을 호스트 캐시에 별도로 준비한다. 일반 설치·계정·음성·Tailscale 설정은 [설치 안내](../setup.md)를 따른다.

원격 웹 공개 제공자 선택은 [한국어 원격 호스팅 설정](../docs/remote-hosting.md)을 참조한다.

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

Ubuntu 24.04 x64 host GPU drivers are **host OS dependencies**, not files
to copy to an NTFS portable SSD. For AMD RX 6800 XT, the standard Linux
`amdgpu` kernel driver plus Mesa RADV user-space Vulkan driver is a
supported route. Check the Linux host:

```bash
python3 portable/check-linux-gpu.py --require
```

To explicitly install Mesa Vulkan runtime packages on an **AMD/Intel**
Ubuntu host (requires sudo, internet and confirmation of package changes):

```bash
bash portable/prepare-portable-linux.sh --install-vulkan
```

NVIDIA hosts require the matching NVIDIA kernel/user-space Vulkan driver
(e.g. via Ubuntu's `ubuntu-drivers` mechanism), not the Mesa RADV driver.
The provisioning command does not attempt an unsolicited NVIDIA driver
installation. After installing/upgrading GPU drivers, reboot when required.
If `vulkaninfo` finds only software rendering (llvmpipe/lavapipe), the
GPU preflight fails instead of silently allowing a CPU Vulkan device.

**Important**: This verifies a real Vulkan-capable GPU is detected;
it does not prove MAI's inference engine actually offloads layers to it.
On the Ollama-backed dev branch, actual AMD GPU acceleration depends
on the bundled Ollama GPU backend and its host prerequisites.
On dev2 llama.cpp Vulkan needs a separate actual model load and inference test.
Check runtime logs / utilization after starting MAI.

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


## Optional llama.cpp router (dev; Ollama remains the default)

This is **opt-in** and does not replace `portable/start-portable.ps1` or change MAI's agent, prompts, verifier stages, and Ollama behavior. No KV save/restore is introduced. The llama.cpp router permits **only one loaded model at a time**: main remains loaded during tool rounds until the provisional answer, then verifier takes over; main reloads for corrections with full saved conversation/tool history and latest rejected provisional answer plus verifier feedback. The router is one public API server (`127.0.0.1:8080`) and may spawn separate per-model child processes internally.

1. Create the Windows pinned sources manifest **once** (do not overwrite an existing manifest automatically):
   ```powershell
   python portable/resolve_portable_sources.py --include-llamacpp
   ```
   Review `portable/portable-sources.json` for exact URLs, versions, and publisher checksums. It includes official **Python 3.11, uv, Ollama, and llama.cpp Vulkan Windows x64 release archives**. Ollama is still downloaded by the existing installer even when running llama.cpp; there is no requirement to **start** Ollama in llama.cpp mode. The installer additionally installs MAI and its `pyproject.toml` Python dependencies (and transitive dependencies); `-InstallChromium` downloads Chromium separately. **It does not download GGUF models or mmproj files.**
2. Run the existing installer (use the previously generated and reviewed manifest):
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/prepare-portable.ps1 -Manifest ./portable/portable-sources.json
   ```
3. Put GGUFs in `portable/portable-models/gguf/`. Download public models with Hugging Face CLI:
   ```powershell
   python -m pip install --upgrade huggingface_hub
   hf download OWNER/REPO MODEL.gguf --local-dir "./portable/portable-models/gguf"
   ```
   Replace the placeholder owner/repo and filename with the actual model listing; download any required matching `mmproj` separately.
4. Start the **optional** single-router launcher (the existing Ollama command stays unchanged):
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/start-portable-llamacpp-router.ps1
   ```
   Set the GGUF paths **only in project-root `settings.yaml`** (no model-path command-line switches):
   ```yaml
   LLAMACPP_MAIN_GGUF_PATH: "portable/portable-models/gguf/Ornith-1.5-9B-Q4_K_M.gguf"
   LLAMACPP_VERIFIER_GGUF_PATH: "portable/portable-models/gguf/verifier.gguf"
   # LLAMACPP_MAIN_MMPROJ_PATH: "portable/portable-models/gguf/mmproj-Ornith-1.5-9B-BF16.gguf"
   LLAMACPP_MAIN_VISION: false
   # LLAMACPP_VISION_GGUF_PATH: "portable/portable-models/gguf/vision.gguf"
   # LLAMACPP_VISION_MMPROJ_PATH: "portable/portable-models/gguf/vision-mmproj.gguf"
   ```
   These are example filenames, not downloaded files. Replace both required paths with existing GGUFs. Main projector and separate vision-model settings are optional. When the main model supports integrated vision without a separate projector, set `LLAMACPP_MAIN_VISION: true`; this merely enables MAI image forwarding, not model compatibility. The router enforces only one resident model; no `-ModelsMax 2` or `-ModelsMax 3` mode is supported. Settings paths are resolved from the MAI project root, not the shell's working directory; the resolver uses the same strict YAML parser as MAI. The launcher generates an untracked `mai-models.generated.ini` in `portable/portable-runtimes/llamacpp/`. It sets **MAIN_MODEL=mai-main**, **VERIFIER_MODEL=mai-verifier**, optionally **VISION_MODEL=mai-vision** and connects all through `LLAMACPP_HOST=http://127.0.0.1:8080`. The main image input uses standard OpenAI `image_url` payloads when `LLAMACPP_MAIN_MMPROJ_PATH` is set or `LLAMACPP_MAIN_VISION: true` is configured.
   
   Projector presence is a **configuration assertion**, not proof the selected model supports images; end-to-end image inference must be checked. The verified GGUF magic only confirms the file container format. A router response from `/v1/models` confirms registration, not inference readiness. For main/verifier tool/JSON contracts, conduct an actual end-to-end smoke test.
   
   `--models-max 1` limits llama.cpp to a single loaded model. Main context KV may be reused within its uninterrupted tool rounds, but loading the verifier evicts the main model and its KV; corrections reconstruct context from MAI message/tool history, not from old KV. Larger contexts (default 16384) increase GPU memory usage. Roles are separate models, but **verifier behavior and prompts are unchanged**.
   
   **Important:** `dev`'s native Ubuntu 24.04 Portable preparation script currently provisions the **Ollama** Linux binary. This new llama.cpp router launcher is **Windows-only until Linux llama.cpp binary provisioning and a native Linux launcher are added and tested**. Do not run a Windows llama-server binary on Linux.

### Regression checks (before merge)

```powershell
python -m pytest -q tests/test_dev_optional_llamacpp_router.py tests/test_llamacpp_adapter.py tests/test_main_model_image_input.py tests/test_final_grounding_verifier.py tests/test_ollama_adapter.py
python -m pytest -q
```

Then verify original Ollama Portable startup is unchanged and llama.cpp mode performs a real main tool call, a structured Verifier review, a main-image request (when configured), and memory extraction. These hardware integration checks have not been run by this change.


## Ubuntu 24.04: optional llama.cpp router Portable (dev)

The existing `prepare-portable-linux.sh` and `start-portable-linux.sh` continue to support **Ollama**. This is an **additional** backend, not a replacement. MAI's agent, verifier prompts, memory, tools and Ollama model files are unchanged. Main and verifier are separate GGUFs behind one llama.cpp router; only one is resident at a time.

1. While online, create the publisher-digest-pinned manifest with llama.cpp:
   ```bash
   python3 portable/resolve_portable_sources_linux.py --include-llamacpp
   ```
   This command refuses to overwrite an existing `portable-sources-linux.json`; preserve/rename the old manifest first and review the new one before provisioning.
2. On the Ubuntu 24.04 x86-64 host, prepare the Linux host cache (native executable and Python packages, separate from Windows NTFS runtimes):
   ```bash
   bash portable/prepare-portable-linux.sh --with-llamacpp
   ```
   Optionally add `--install-vulkan` to install host Mesa Vulkan dependencies on AMD/Intel. The existing hardware Vulkan preflight requires `vulkaninfo` and a non-software GPU; host driver installation, mount permissions and native libraries cannot be bundled as an identical cross-OS binary.
3. Put the main, projector and verifier GGUF files in the locations already configured in **`settings.yaml`**: `LLAMACPP_MAIN_GGUF_PATH`, `LLAMACPP_MAIN_MMPROJ_PATH`, `LLAMACPP_VERIFIER_GGUF_PATH`. The same paths work on Windows and Linux if relative to the project root.
4. Run the optional Linux router:
   ```bash
   bash portable/start-portable-llamacpp-linux.sh
   ```
   Only `1` resident model is supported, including when a separate Vision GGUF is configured. The script creates a private per-run router INI in the Linux host cache, validates GGUF headers and exposes the same `mai-main`, `mai-verifier`, optional `mai-vision` aliases as the Windows launcher. It does not reuse an occupied port or silently fall back to Ollama.

**Host portability boundary:** Linux native Python, llama.cpp binaries and Python dependencies are cached under `~/.cache/mai-portable/<path-id>` (or `XDG_CACHE_HOME`). Moving the SSD to an **unprepared** Linux computer still requires the initial provisioning step, executable host tools, writable data filesystem, Vulkan drivers and adequate GPU/RAM; this is not yet guaranteed plug-and-run on arbitrary Linux hardware. Linux remote-agent bootstrap is still Windows PowerShell-only; a Linux MAI host can use local tools, but attaching a **Linux remote target** requires a separate remote worker implementation. Playwright Chromium and audio/microphone capabilities have native host dependencies.

Before merging, run `python -m pytest -q` on Ubuntu and test actual Vulkan inference, image input, main tool calls, verifier JSON output, local file/code/CSV/document/terminal tool paths, browser Chromium, and remote Windows worker separately. No live Ubuntu inference has been run for this addition.


### llama.cpp mode without Ollama installation or active Ollama server

The llama.cpp router starts its own local API server and uses `LlamaCppAdapter` over HTTP. It does **not** need a running Ollama program, Ollama daemon, Ollama model store, or the `ollama` Python package at MAI startup. The SDK is imported and an `AsyncClient` is created **only** if `LLM_BACKEND=ollama`; the llama.cpp path does not construct one. Ollama mode remains available and requires its own Python dependency plus a running Ollama server.

If a prior Portable Python environment is missing dependencies unrelated to Ollama, provision it with the existing `prepare-portable.ps1` procedure. Avoid installing or starting the Ollama application merely to run the llama.cpp router.

Regression tests (run in the Portable Python environment):
```powershell
./portable/portable-runtimes/python/python.exe -m pytest -q tests/test_optional_ollama_dependency.py tests/test_llamacpp_adapter.py tests/test_ollama_adapter.py tests/test_final_grounding_verifier.py
```


### llama.cpp 모델 전환 확인 (Main ↔ Verifier)

`--models-max 1` 제한에 더하여 MAI의 llama.cpp 어댑터는 **다른 GGUF를 호출하기 직전** `GET /models`로 현재 상태를 확인하고, 기존 활성 모델에 `POST /models/unload`를 요청한 뒤 `GET /models`에서 해당 모델의 상태가 명확히 `unloaded`가 될 때까지 기다린다. 기본 확인 제한은 30초다. 언로드 요청 실패·응답 형식 오류·완료 확인 실패 시 **다음 모델을 호출하지 않고 오류를 반환**한다. 같은 모델을 연속 호출할 때는 언로드하지 않으며, 따라서 메인 모델의 도구 턴 중 KV 재사용 기회가 유지된다.

이 동작은 메인, Verifier, 기억 추출, 선택형 Vision 도구가 동일하게 사용한다. Verifier 이후 메인을 호출하는 경우에도 역방향 확인을 수행한다. **GPU 메모리 사용량이나 드라이버의 메모리 반환 상태를 별도 측정하지 않는다.** 상태 `unloaded` 확인은 라우터가 보고하는 언로드 완료이며, 시스템 전체의 VRAM 상태를 보증하지 않는다. 다른 프로세스의 GPU 사용량도 통제하지 않는다.

테스트: `python -m pytest -q tests/test_llamacpp_confirmed_unload.py tests/test_llamacpp_adapter.py tests/test_rejected_draft_failure_display.py`. 실제 Windows/Linux llama.cpp 빌드에서 `/models` 상태 전환을 확인하기 전까지는 실기기 검증 완료로 취급하지 않는다.
