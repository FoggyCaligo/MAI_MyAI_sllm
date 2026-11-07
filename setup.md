# MAI dev2 설치와 설정

이 브랜치의 기본 구성은 **GGUF + llama.cpp 기반 MAI**다. 기본 설정은 메인 GGUF 한 개로 실행하며, 추가 GGUF를 지정하면 실험적 llama.cpp 라우터가 메인·Verifier·Vision 모델을 필요할 때 교대 적재한다(`--models-max 1`). Ollama 클라이언트는 MAI 구동의 필수 구성요소가 아니다. 별도 모델 라우팅은 llama.cpp 바이너리 호환성·실기기 검증이 필요하다.

## 계정

```powershell
Copy-Item .env.example .env
```

`.env`의 `OWNER_USERS`에서 `user_pw`를 개인 비밀번호로 변경한다.
기존 DB가 있다면 사용자의 `db_id`를 변경하지 않는다.
시험 계정을 사용하지 않으면 `TRIAL_USERS=[]`로 지정하고,
외부 공개를 원하지 않는다면 `settings.yaml`의 `REMOTE_HOSTING_PROVIDER: none`으로 변경한다. 기본값은 `tailscale`이며 Tailscale 설치·로그인·Funnel 허용이 선행되어야 실제 공개가 가능하다.
비밀번호, 개인 DB, API 토큰은 Git에 커밋하지 않는다.

## 모델

`settings.yaml`에서 모델을 선택하는 값은 다음 하나다.

```yaml
MODEL_GGUF_PATH: "portable/portable-models/gguf/active.gguf"
```

이 값은 **메인 GGUF 파일의 경로**다. 상대경로는 MAI 프로젝트 루트 기준이며, 절대경로도 가능하다. 내부 메인 모델 별칭은 `mai-model`이다. Portable 실행 시 `-GgufPath`로 이번 실행의 메인 경로를 변경할 수 있다. 선택적 Vision/Verifier 모델은 아래의 별도 설정으로 지정한다.

## GGUF 경로 오류와 Ollama sha256 Blob

문서의 `D:\\models\\my-model.gguf`(또는 `실제모델.gguf`)는 **설명용 가상 경로**이며 파일을 제공한다는 의미가 아니다. `-ModelFile`에는 직접 내려받거나 준비한 **실제 GGUF 파일의 경로**를 지정한다. 이미 `portable/portable-models/gguf/active.gguf`에 사용할 GGUF가 있으면 `-ModelFile` 없이 설치해도 된다.

현재 기본 경로가 이미 존재하면 외부 파일을 다시 지정할 필요가 없다. `-ModelFile`은 새로운 GGUF 원본을 설정 경로로 복사할 때만 사용한다.

Ollama의 `sha256-...` 파일은 Blob 저장소 항목이므로 **확장자가 없다는 이유만으로 GGUF가 아니라고 단정할 수 없으며**, 반대로 모두 GGUF인 것도 아니다. `ollama show 모델명 --modelfile`로 `FROM`이 가리키는 파일을 확인하고 처음 4바이트가 `GGUF`인지 검사한 뒤, 해당 모델 데이터 파일만 정확히 선택하여 복사해야 한다. 템플릿이나 부가 설정은 GGUF 파일에 포함되지 않을 수 있다.

`start-portable.ps1`은 YAML 경로를 별도 `portable/resolve_model_path.py`에서 읽는다. 경로가 없으면 모델 파일 위치를 분명히 알려주는 오류를 출력한다. 이미 설치한 Python/uv/llama.cpp를 모델 경로 오류만으로 새로 다운로드할 필요는 없다.

## GGUF 모델 준비: Ollama 없이 다운로드 (권장)

**자동설치 스크립트는 모델 가중치를 다운로드하지 않는다.** GGUF 모델은 별도로 준비한 후 `settings.yaml`의 경로를 지정한다. GGUF가 공개되어 있는 저장소라면 Ollama 설치 없이 Hugging Face 공식 CLI로 파일을 다운로드할 수 있다. 공식 안내: https://huggingface.co/docs/huggingface_hub/en/guides/cli

PowerShell에서 (MAI 프로젝트 루트):

```powershell
# 최초 1회: 다운로드 전용 CLI 설치 (MAI 실행 시 필요한 패키지는 아님)
python -m pip install --upgrade huggingface_hub
hf download --help

# Hugging Face 모델 페이지의 Files 목록에서 정확한 저장소 ID와 GGUF 파일명을 확인한다.
# 예: 저장소 ID가 OWNER/REPO이고 파일명이 MODEL-Q4_K_M.gguf일 때만 아래처럼 입력
hf download OWNER/REPO MODEL-Q4_K_M.gguf --local-dir "./portable/portable-models/gguf"
```

`OWNER/REPO`와 `MODEL-Q4_K_M.gguf`는 **사용자가 선택한 공개 모델의 실제 값으로 바꿔야 하는 자리표시자**이며 특정 파일의 존재를 보증하지 않는다. 분할 GGUF라면 모든 파트를 받아야 하며, 비전 모델이 별도 projector를 요구하면 같은 모델에 호환되는 projector도 별도로 받는다. 비공개·라이선스 승인 모델은 해당 제공자의 인증이 필요하다. 버전 고정이 필요하면 `hf download ... --revision <커밋 SHA>`를 지정한다. 직접 다운로드 시 다운로드 공급망/라이선스/검증 정보는 모델 제공자의 페이지를 확인한다.

다운로드한 파일은 `settings.yaml`에 정확한 이름으로 지정한다. 예를 들어 실제 파일명이 `MODEL-Q4_K_M.gguf`라면:

```yaml
MODEL_GGUF_PATH: "portable/portable-models/gguf/MODEL-Q4_K_M.gguf"
```

여기서 `MODEL_GGUF_PATH`를 모델에 맞춰 변경하면 `-ModelFile`로 `active.gguf`에 복사할 필요가 없다. 파일 헤더가 GGUF인지만 확인하는 것은 **모델의 정상 작동·라이선스·안전성을 검증한다는 뜻이 아니다.**

## 대안: 이미 Ollama에 설치된 GGUF를 복사

Ollama가 설치된 컴퓨터에서는 `ollama show <모델명> --modelfile`의 `FROM` 경로를 확인한다. `sha256-...` blob에는 모델 본체 이외의 템플릿·어댑터·projector 데이터가 있을 수 있으므로 확장자가 없더라도 처음 4바이트가 `GGUF`인 **올바른 모델 본체**를 선택한다.

```powershell
ollama show gemma3:12b --modelfile
# FROM 항목이 가리키는 실제 파일 경로를 확인해 $source에 넣는다.
$source = 'C:\Users\USER\.ollama\models\blobs\sha256-실제해시'
$target = '.\portable\portable-models\gguf\gemma3-12b.gguf'
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Source does not exist: $source" }
if (Test-Path -LiteralPath $target) { throw "Refusing to replace: $target" }
$stream = [IO.File]::OpenRead($source)
try {
    $magic = [byte[]]::new(4)
    if ($stream.Read($magic, 0, 4) -ne 4 -or
        [Text.Encoding]::ASCII.GetString($magic) -cne 'GGUF') { throw 'Not a GGUF blob' }
} finally { $stream.Dispose() }
New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
Copy-Item -LiteralPath $source -Destination $target -ErrorAction Stop
if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne
    (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash) {
    throw 'GGUF copy checksum mismatch'
}
```

이후 `MODEL_GGUF_PATH`를 `portable/portable-models/gguf/gemma3-12b.gguf`로 지정한다. 이 방법은 원본 Ollama blob을 **복사만** 하며 원본은 삭제하지 않는다. 일부 Ollama 모델은 단일 GGUF 본체가 아닐 수 있고, Ollama의 템플릿·파라미터가 자동 복제되는 것은 아니므로 llama.cpp 호환성을 따로 확인한다.

## Windows Portable 자동설치: 정확히 무엇을 다운로드하나?

`resolve_portable_sources.py`는 런타임 실행파일을 다운로드하지 않는다. GitHub 릴리스 API에서 **아래 세 배포 항목의 정확한 URL과 제공자 선언 SHA-256**을 조회하여 `portable/portable-sources.json`을 만든다. 기존 파일은 자동 덮어쓰지 않는다.

| 매니페스트 키 | 공식 GitHub 저장소 | 선택 파일 및 목적 | 설치 위치 |
|---|---|---|---|
| `python` | `astral-sh/python-build-standalone` | Windows x64 CPython 3.11 `install_only.tar.gz`; MAI 전용 Python | `portable/portable-runtimes/python/` |
| `uv` | `astral-sh/uv` | `uv-x86_64-pc-windows-msvc.zip`; Python 의존성 설치기 | `portable/portable-runtimes/uv/` |
| `llamacpp` | `ggml-org/llama.cpp` | `llama-*-bin-win-vulkan-x64.zip`; GGUF 추론 서버와 Vulkan 라이브러리 | `portable/portable-runtimes/llamacpp/` |

버전은 고정 상수가 아니라 **스크립트 실행 당시 선택된 공식 릴리스**다. 따라서 정확한 버전·파일 URL·검증값은 생성된 매니페스트에서 확인해야 한다.

```powershell
Get-Content ./portable/portable-sources.json -Raw
```

`prepare-portable.ps1`은 (1) 매니페스트의 URL로 세 배포 압축파일을 `portable/portable-downloads/`에 받고, (2) 제공자 선언 SHA-256과 다운로드 파일의 해시를 대조하고, (3) 위의 런타임 경로에 설치한다. 기존 설치 기록이 일치하는 런타임은 재사용한다.

**추가로 내려받는 Python 의존성:** `uv pip install --python <Portable Python> <MAI 프로젝트>`가 `pyproject.toml`에 선언된 다음 패키지(및 각 패키지의 하위 의존성)를 설치한다.

- 웹/API: `markdown-it-py`, `beautifulsoup4`(Python import 이름 `bs4`), `ddgs`, `fastapi`, `httpx`, `pydantic`, `python-dotenv`, `PyYAML`, `python-multipart`, `uvicorn`
- 브라우저: `playwright`
- 문서/파일: `openpyxl`, `pypdf`, `python-docx`, `python-pptx`
- 음성/수치: `speakeronnx`, `vadonnx`, `scipy`, `faster-whisper`, `numpy`
- 코드 분석: `tree-sitter`, `tree-sitter-javascript`, `tree-sitter-typescript`, `tree-sitter-c`, `tree-sitter-cpp`, `tree-sitter-java`, `tree-sitter-go`, `tree-sitter-rust`, `tree-sitter-ruby`
- 별도 Git 소스: `sentence-breaker`는 `git+https://github.com/FoggyCaligo/Sentence_Breaker.git@main`에서 설치한다.

현재 `uv pip install`은 잠금파일/전체 패키지 SHA 해시로 고정하는 방식이 아니며, `sentence-breaker`는 고정 커밋이 아니라 변경 가능한 `main`을 참조한다. 따라서 **세 런타임 아카이브의 SHA 검증이 모든 하위 Python 패키지까지 동일하게 검증한다는 뜻은 아니다.** 설치 전에 `pyproject.toml`을 확인한다. `dev` 개발용 선택 의존성인 `pytest`, `jsonschema`는 일반 Portable 설치 명령의 `[dev]` 옵션 없이 자동 설치되는 항목이 아니다.

**Playwright의 Chromium은 기본 명령으로 다운로드하지 않는다.** JavaScript 기반 웹사이트 조작(`browser_open`, `browser_click` 등)이 필요하면 `-InstallChromium`을 지정하여 `python -m playwright install chromium`을 실행한다. 이때 Playwright가 지정한 브라우저 배포 파일은 `portable/portable-runtimes/playwright-browsers/`에 설치된다. 웹페이지 단순 검색·fetch와는 별개다.

`prepare-portable.ps1`이 **다운로드하지 않는 것:** 메인/Verifier/Vision GGUF 및 projector, Ollama 클라이언트, Tailscale, cloudflared, AMD 드라이버, Windows 자체 구성요소, Hugging Face CLI. `-ModelFile`은 외부 모델 파일을 새로 받는 옵션이 아니라 **사용자가 준비한 GGUF를 설정된 경로에 복사하는 옵션**이다.

```powershell
# 실행 전: 모델을 settings.yaml 경로에 미리 준비해 두어야 한다.
python portable/resolve_portable_sources.py

# 브라우저 조작 기능을 함께 준비하려면 -InstallChromium 추가
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/prepare-portable.ps1 -Manifest ./portable/portable-sources.json -InstallChromium

# 준비된 실행 환경과 GGUF로 MAI 시작
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/start-portable.ps1
```

이미 `portable-sources.json`이 있다면 첫 번째 명령을 다시 실행할 필요가 없으며, 재생성 시 기존 매니페스트를 별도로 보존한다. `settings.yaml`의 모델 경로가 실제 파일을 가리키지 않으면 설치 스크립트는 다운로드 전에 중단한다. **설치 전체가 완전한 오프라인 실행이라는 뜻은 아니다.** 런타임·Python 패키지·선택 Chromium 설치에는 네트워크가 필요하다.

## Windows Portable 설치

모델 경로는 `settings.yaml`의 `MODEL_GGUF_PATH` 하나를 기준으로 한다. 기본값은 `portable/portable-models/gguf/active.gguf`이며, 실제 GGUF가 해당 경로에 존재하면 `-ModelFile`을 지정하지 않는다. 예시 경로는 실제 모델을 제공하지 않는다.

```powershell
python portable/resolve_portable_sources.py
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/prepare-portable.ps1 -Manifest ./portable/portable-sources.json
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/start-portable.ps1
```

설치 중 모델이 없다면 런타임 다운로드 전에 중단한다. 이미 Ollama의 모델 본체 SHA blob을 GGUF로 확인했다면 `active.gguf`로 **복사**한 후 실행할 수 있다. 새 원본 GGUF 파일을 복사하고 싶을 때만 다음처럼 `-ModelFile`을 선택적으로 사용한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/prepare-portable.ps1 -Manifest ./portable/portable-sources.json -ModelFile 'C:\path\to\model.gguf'
```

이 옵션은 원본 파일을 `MODEL_GGUF_PATH`로 복사하며 기존 목적지 파일은 임의로 덮어쓰지 않는다. 별도 이름이나 위치를 쓰려면 먼저 `settings.yaml`을 변경한다. 설치 초기 경로 해석에는 **PyYAML이 설치된 Python**이 필요하며, Portable Python이 이미 존재한다면 우선 사용한다.

매니페스트에는 공식 릴리스의 특정 Python·uv·llama.cpp Vulkan 바이너리와 제공자가 선언한 SHA-256이 포함된다. 엔진과 GGUF는 분리돼 있으므로 엔진을 교체해도 모델과 DB는 유지된다. 서버는 `http://127.0.0.1:8000`에서 접근한다. 실제 모델 로드, Vulkan GPU 추론은 별도 검증해야 한다.

## 원격 웹 호스팅: Tailscale(기본) / Cloudflare

```yaml
REMOTE_HOSTING_PROVIDER: tailscale     # tailscale | cloudflare | none
```

Tailscale Funnel은 호스트에 Tailscale을 설치·로그인하고 Funnel 권한을 허용해야 공개된다. `cloudflare`를 선택하려면 호스트에 cloudflared를 설치하고, Cloudflare Dashboard에서 관리형 Named Tunnel 및 `http://127.0.0.1:8000`으로 연결할 공개 호스트명을 만든 후 `.env`에 `CLOUDFLARE_TUNNEL_TOKEN`을 지정한다. `none`은 이번 MAI 프로세스의 원격 공개 실행을 건너뛴다.

기존 `TAILSCALE_FUNNEL`은 사용하지 않는 구형 설정이므로 `.env`에 남아 있다면 삭제할 수 있다. Tailscale Funnel은 MAI 종료 이후에도 유지될 수 있으므로 다른 제공자로 전환 시 `tailscale funnel status`로 기존 공개 경로를 확인하고 필요한 경우 명시적으로 해제한다. 자세한 내용은 [한국어 원격 호스팅 설정](docs/remote-hosting.md)을 참고한다.

호스팅과 원격 PC 명령 실행 세션은 별개다. 원격 PC 연결 시간 정책은 각 브랜치의 원격 세션 코드를 따른다.

## Verifier 제어 (dev2)

`settings.yaml`에서 답변 검증을 켜고 끌 수 있다.

```yaml
MAI_VERIFIER_ENABLED: true          # false: 최종 숫자/사실 검증 요청 자체를 건너뜀
MAI_VERIFIER_BLOCKING_MODE: critical # critical | strict
MAI_ANSWER_QUALITY_REVIEW: false    # 별도 품질/요청 충족 검토 (기존 옵션)
```

- `MAI_VERIFIER_ENABLED: false`: 최종 Verifier를 생성하지 않고 숫자 검증과 사실 검증, 역할별 KV 전환을 건너뛴다. 메인 모델·도구·후속 메모리 처리까지 끄는 옵션은 아니다. 메인 모델 답변이 자동으로 사실 검증되는 것은 아니므로 실험 시 로그와 답변을 직접 비교해야 한다.
- `critical`(기본): 관측된 근거와 **직접 충돌**하는 사실 주장, 요청한 실행 결과를 검증 없이 완료했다고 단정, 결과가 반박하는 성공 주장, 명백한 핵심 요청 불일치가 Verifier에서 판정된 경우만 재수정 대상으로 삼는다. 단순 근거 부재, 수치 동등성 불확실성, 답변 완성도 부족은 거절이 아니라 감사 로그의 경고로 보존한다. *오류의 완벽한 감지나 교정은 보증하지 않는다.*
- `strict`: 기존의 강한 근거 검증 및 거절 동작을 유지한다. 비교 실험용.
- `MAI_ANSWER_QUALITY_REVIEW: false`이면 별도 품질/요청 충족 검토 단계는 실행하지 않는다. `critical` 설정만으로 그 단계가 자동 실행되지는 않는다.
- 기존 `.env` 또는 프로세스 환경변수에 동일한 키가 존재하면 그 값이 YAML보다 우선한다. 설정 변경 후 MAI를 재시작한다.

**KV 캐시 범위:** 단일 모델 모드에서는 모델 정체성에 연결된 메인/숫자/최종 검증 체크포인트를 사용한다. 별도 Vision/Verifier GGUF를 지정해 라우터 모드가 활성화되면 기존 슬롯 KV 저장/복원 대신 대화 문맥을 재구성한다. 모델별 KV를 서로 공유하지 않는다. 실기기 모델 교대 적재 검증은 남아 있다.

## GGUF 모델 교대 적재와 이미지 인식 (실험 기능)

llama.cpp 라우터가 `--models-max 1`로 **메인·Verifier·비전 모델 중 한 개만** 적재한다. 새 요청의 모델이 다르면 기존 모델을 내리고 해당 모델을 올린다. 모델별 KV 체크포인트는 서로 공유하지 않으며, 라우터 모드에서는 기존 단일 서버 전용 `RoleCacheRun`을 사용하지 않는다. 메인 상태는 대화·도구 문맥으로 재구성된다. 모델 교체 시 재적재 시간은 발생할 수 있다.

```yaml
MODEL_GGUF_PATH: "portable/portable-models/gguf/active.gguf"
MAI_VERIFIER_ENABLED: true
MAI_VERIFIER_BLOCKING_MODE: critical
# 다음 설정은 필요한 모델을 실제 복사한 뒤 주석을 해제한다.
# VERIFIER_GGUF_PATH: "portable/portable-models/gguf/lfm2.5-2.6b.gguf"
# VISION_GGUF_PATH: "portable/portable-models/gguf/gemma3-12b.gguf"
# VISION_MMPROJ_PATH: "portable/portable-models/gguf/mmproj-gemma3.gguf"
```

- `VISION_GGUF_PATH`: 이미지 모델의 **본체 GGUF**. 설정하면 이미지 첨부 시 먼저 해당 모델에 이미지와 질문을 전달해 관찰 결과를 얻고, 메인 모델이 그 결과를 사용해 답한다. 원본 이미지는 현재 메인 모델에게 직접 전달하지 않는다.
- `VISION_MMPROJ_PATH`: 해당 이미지 모델에 적합한 **별도 멀티모달 projector GGUF**가 필요한 경우 설정. Ollama의 SHA blob에서 헤더와 메타데이터를 확인해 복사한다. 모델과 projector를 무작정 합치거나 다른 모델용 projector를 사용하지 않는다.
- `VERIFIER_GGUF_PATH`: LFM 같은 경량 Verifier 모델의 GGUF 파일(선택). 설정한 경우 최종 숫자·사실 검증에서 별도 모델을 사용하며 기존 `critical`/ `strict` 정책과 `MAI_VERIFIER_ENABLED`을 유지한다.
- `MAI_VERIFIER_ENABLED: false`: 검증 모델을 **사용하지 않음**. 다른 이미지 모델을 설정했다면 비전 전환만 작동한다.
- PNG/JPEG/WebP 이미지는 계정별 업로드 영역에서만 읽고 1장당 최대 20MiB를 처리한다. GIF는 현재 이미지 인식에 지원하지 않는다. 분석 실패를 근거 없이 텍스트 추론 성공으로 처리하지 않고 오류를 알린다.
- `.env` 또는 환경변수가 YAML보다 우선하며, 모델 파일의 확장자·GGUF 헤더·실제 존재 여부를 Portable 시작 전에 확인한다.
- Windows/Ubuntu Portable 런처는 llama.cpp의 **router** 기능을 사용하므로 해당 옵션을 지원하는 Vulkan 빌드가 필요하다. 버전에 따라 모델·projector 호환성이 다르며 **실기기 로드·이미지 추론·정확한 모델 언로드는 아직 검증하지 않았다.**
- 최초 로컬 테스트는 Verifier를 `false`로 두고 단일 이미지에서 메인 ↔ 비전 전환을 점검한 뒤, 경량 Verifier 모델을 추가하는 순서를 추천한다.

검증 명령:

```powershell
python -m pytest -q tests/test_dev2_vision_router.py tests/test_dev2_verifier_policy.py
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/start-portable.ps1
```

**경고:** 라우터는 모델 1개만 상주하도록 제한하지만, 파일 로드 과정에서 GPU 여유 메모리가 충분한지는 실제 VRAM 측정이 필요하다. 모델을 언로드한 뒤 동일 KV를 다른 모델에 적용하지 않는다.

## 실행 제약

- `REMOTE_HOSTING_PROVIDER` 기본값: `tailscale` (`none`/`cloudflare`도 가능)
- `LLAMACPP_HOST` 기본값: `http://127.0.0.1:8080`
- 숫자/사실 검증 및 메모리 사실 추출: 같은 모델을 순차 실행
- `MAI_MODEL_REQUEST_TIMEOUT_SECONDS`: 추론 요청 제한 시간
- `MAI_ANSWER_QUALITY_REVIEW`: 사실검증 이후 선택적인 품질 검토
- `MAX_INLINE_TOOL_RESULT_CHARS`, `SESSION_HISTORY_MESSAGES`: 도구·대화 문맥 처리
- `VOICE_*`: 음성 기능
- 컨텍스트 크기는 MAI가 임의로 32K 등으로 줄이지 않는다.

실행 중 다른 프로세스에서 llama.cpp의 동일한 슬롯을 사용하면
KV 캐시가 섞일 수 있으므로 서버 접근을 분리한다.
이미지 인식은 선택적 Vision GGUF가 설정된 라우터 모드에서만 시도한다.
가중치 적재 여부, VRAM·RAM, GPU 전원 안정성은 실제 컴퓨터에서 검증해야 한다.

## 테스트

```bash
python -m pytest -q
```

기존 기록의 `602 passed`는 이 구조 변경 이전 결과다.
새 구조의 테스트는 별도로 실행해 확인해야 한다.

추가 지침은 [Portable 안내](portable/README.md)를 참고한다.
