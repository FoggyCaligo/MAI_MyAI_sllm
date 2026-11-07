# MAI dev2 — Portable 단일 GGUF 모델

dev2는 **하나의 GGUF 파일 + 하나의 llama.cpp 서버**로 실행한다.
Ollama 서비스, 두 번째 Verifier 모델, 별도 Vision 모델, 브라우저 모델 선택기는 사용하지 않는다.
한 모델이 main / numeric / final 검증을 순서대로 처리하고 역할별 KV 상태를 디스크에 저장/복원한다.

원격 웹 공개 제공자 선택은 [한국어 원격 호스팅 설정](../docs/remote-hosting.md)을 참조한다.

## 파일 구조

```text
MAI/
  .env                                # 계정/비밀정보, Git 제외
  settings.yaml                       # MODEL_GGUF_PATH: 사용할 GGUF 파일 경로 1개
  data/                               # 계정별 DB
  portable/
    portable-sources.json             # Python/uv/llama.cpp 특정 릴리스 URL·SHA256
    portable-runtimes/
      python/                         # Portable Python
      uv/
      llamacpp/                       # 업그레이드 가능한 llama-server
    portable-models/gguf/active.gguf  # 단 하나의 선택된 GGUF
    portable-slot-cache/              # 실행별 임시 KV 저장 파일
```

Windows에서는 llama.cpp **Vulkan x64** 바이너리를 사용한다. GPU 드라이버는 별도로 설치해야 한다.
OS/드라이버/모델이 호환되지 않으면 실행이 실패할 수 있다. GPU 메모리 자동 안전 보장은 없다.

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

## GGUF 경로 오류와 Ollama sha256 Blob

문서의 `D:\\models\\my-model.gguf`(또는 `실제모델.gguf`)는 **설명용 가상 경로**이며 파일을 제공한다는 의미가 아니다. `-ModelFile`에는 직접 내려받거나 준비한 **실제 GGUF 파일의 경로**를 지정한다. 이미 `portable/portable-models/gguf/active.gguf`에 사용할 GGUF가 있으면 `-ModelFile` 없이 설치해도 된다.

이미 `settings.yaml`의 `MODEL_GGUF_PATH`에 GGUF가 있으면 `-ModelFile`을 생략한다. 다른 파일에서 가져올 때만 `-ModelFile`을 지정하며, 설치 스크립트는 설정 경로로 복사한다.

Ollama의 `sha256-...` 파일은 Blob 저장소 항목이므로 **확장자가 없다는 이유만으로 GGUF가 아니라고 단정할 수 없으며**, 반대로 모두 GGUF인 것도 아니다. `ollama show 모델명 --modelfile`로 `FROM`이 가리키는 파일을 확인하고 처음 4바이트가 `GGUF`인지 검사한 뒤, 해당 모델 데이터 파일만 정확히 선택하여 복사해야 한다. 템플릿이나 부가 설정은 GGUF 파일에 포함되지 않을 수 있다.

`start-portable.ps1`은 YAML 경로를 별도 `portable/resolve_model_path.py`에서 읽는다. 경로가 없으면 모델 파일 위치를 분명히 알려주는 오류를 출력한다. 이미 설치한 Python/uv/llama.cpp를 모델 경로 오류만으로 새로 다운로드할 필요는 없다.

## Windows 준비

프로젝트 루트에서 모델 경로는 `settings.yaml`의 `MODEL_GGUF_PATH`를 기준으로 한다. 현재 기본값 `portable/portable-models/gguf/active.gguf`가 이미 있으면 복사 옵션 없이 설치한다.

```powershell
# 최초 사용자는 .env.example을 복사한 뒤 계정 비밀번호를 설정한다.
Copy-Item .env.example .env
python portable/resolve_portable_sources.py
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/prepare-portable.ps1 -Manifest ./portable/portable-sources.json
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/start-portable.ps1
```

새 원본 GGUF에서 모델을 가져와야 할 때만 `-ModelFile 'C:\path\to\model.gguf'`를 지정한다. 설치 스크립트는 원본을 `MODEL_GGUF_PATH`에 복사한다. 기존 대상 파일은 덮어쓰지 않으며 교체 시 `-PreserveAndReinstallExisting`로 백업한다. 처음 설치 시 경로 해석에는 PyYAML이 설치된 Python이 필요하다.

기존 Ollama SHA blob을 재활용할 때는 본체의 GGUF 헤더와 실제 모델 파일인지 확인한 다음 `active.gguf`로 복사한다. projector 등 별도 blob은 본체에 합치지 않는다. 현재 모델을 사용하려면 설치 뒤 모델 로드 및 Vulkan 추론을 검증해야 한다.

## llama.cpp만 업그레이드하기

Ollama 업데이트와 llama.cpp 업데이트는 별개의 일이다.

1. MAI와 llama-server가 종료된 것을 확인한다.
2. 새로운 `ggml-org/llama.cpp` Windows Vulkan 공식 릴리스 및 SHA-256으로 매니페스트를 **새로 생성**한다. 기존 매니페스트를 덮어쓰지 않고 안전하게 보존한 뒤 새 매니페스트를 사용한다.
3. 아래 명령으로 이전 실행 엔진을 백업한 뒤 교체한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/prepare-portable.ps1 -Manifest ./portable/portable-sources.json -PreserveAndReinstallExisting
```

이 작업은 GGUF, `.env`, 계정/메모리 DB를 바꾸지 않는다.
기존 `portable-runtimes/llamacpp`은 타임스탬프 백업으로 보존한다.
새 바이너리가 슬롯 API나 도구 호출 계약을 바꿨다면 실제 회귀 테스트 이후에만 사용한다.
**업데이트할 때마다 반드시 모델까지 다시 받을 필요는 없다.**

## 다른 GGUF로 변경

단일 모델이므로 서버 실행 중에 다른 모델을 추가 적재하지 않는다.
종료한 뒤 `settings.yaml`의 `MODEL_GGUF_PATH`를 다른 GGUF 경로로 바꾸거나 `-ModelFile`을 지정해 `active.gguf`를 교체할 수 있다. 시작 시 `-GgufPath`를 명시하면 해당 실행에 한하여 YAML 경로를 덮어쓴다.
파일 교체에는 `-PreserveAndReinstallExisting`을 사용해 기존 파일을 보존할 수 있다.
GGUF 배포처/라이선스를 확인하고 해당 파일을 별도 백업한다.

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

## Linux / Ubuntu 24.04 x64

Linux에서도 Python·uv·llama.cpp Vulkan 바이너리를 호스트 캐시에 배치하고
SSD의 단일 `active.gguf`을 참조한다. 초기 준비:

```bash
python3 portable/resolve_portable_sources_linux.py
bash portable/prepare-portable-linux.sh
bash portable/start-portable-linux.sh
```

배포 자산 해시는 공식 릴리스에서 가져온 값을 사용한다. Linux 호환성은
해당 호스트에서 별도 확인해야 한다.

OS별 공통 도구 인터페이스와 남은 이식성 제한은 [OS 경계 문서](../docs/platform-boundary.md)를 참조한다.

## 유의사항

- 마이크·원격 PC·계정·도구 기능은 그대로 남지만 **이미지 첨부 직접 인식은 현재 llama.cpp 단일 모델 경로에 연결되지 않았다.** 별도의 비전 모델을 자동 적재하지 않으며, 지원되지 않는 이미지를 보내면 명시적 오류가 난다.
- 컨텍스트를 32K 등으로 인위적으로 제한하지 않았다. 물리적 VRAM/RAM 한계는 존재한다.
- KV 캐시 파일은 대화에서 파생된 내용을 포함할 수 있다. 정상 작업 종료 시 삭제하고, 강제 전원 차단 이후 남은 파일은 서버 종료를 확인한 후 정리한다.
- 포터블 설치/백업은 GPU 전원 차단을 해결하지 않는다. 전원 차단이 반복됐다면 전원공급장치, GPU 드라이버, Windows 이벤트 로그 등을 우선 확인한다.
- 사용 전 `python -m pytest -q`로 테스트하고, **실제 Windows Vulkan 서버에서는 별도 성능/안정성 검증**이 필요하다.
