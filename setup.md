# MAI dev2 설치와 설정

이 브랜치는 **포터블 단일 GGUF 전용 MAI**다.
추론은 하나의 `llama-server` 프로세스가 담당한다.
Ollama 실행 경로나 브라우저 모델 선택, 별도 Verifier·Vision 모델은 사용하지 않는다.
일반적인 두 모델 구성은 `dev` 브랜치에서 유지한다.

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

이 값은 **실제로 실행할 GGUF 파일의 경로**다. 상대경로는 MAI 프로젝트 루트 기준이며, 절대경로도 가능하다. 내부 모델 별칭은 `mai-model`로 고정된다. Portable 실행 시 `-GgufPath`로 일시적인 다른 경로를 지정할 수도 있다. 한 번에 모델 한 개만 올린다.

## GGUF 경로 오류와 Ollama sha256 Blob

문서의 `D:\\models\\my-model.gguf`(또는 `실제모델.gguf`)는 **설명용 가상 경로**이며 파일을 제공한다는 의미가 아니다. `-ModelFile`에는 직접 내려받거나 준비한 **실제 GGUF 파일의 경로**를 지정한다. 이미 `portable/portable-models/gguf/active.gguf`에 사용할 GGUF가 있으면 `-ModelFile` 없이 설치해도 된다.

현재 기본 경로가 이미 존재하면 외부 파일을 다시 지정할 필요가 없다. `-ModelFile`은 새로운 GGUF 원본을 설정 경로로 복사할 때만 사용한다.

Ollama의 `sha256-...` 파일은 Blob 저장소 항목이므로 **확장자가 없다는 이유만으로 GGUF가 아니라고 단정할 수 없으며**, 반대로 모두 GGUF인 것도 아니다. `ollama show 모델명 --modelfile`로 `FROM`이 가리키는 파일을 확인하고 처음 4바이트가 `GGUF`인지 검사한 뒤, 해당 모델 데이터 파일만 정확히 선택하여 복사해야 한다. 템플릿이나 부가 설정은 GGUF 파일에 포함되지 않을 수 있다.

`start-portable.ps1`은 YAML 경로를 별도 `portable/resolve_model_path.py`에서 읽는다. 경로가 없으면 모델 파일 위치를 분명히 알려주는 오류를 출력한다. 이미 설치한 Python/uv/llama.cpp를 모델 경로 오류만으로 새로 다운로드할 필요는 없다.

## GGUF 모델 다운로드 — Ollama 없이 설치하기

MAI의 `prepare-portable.ps1`은 **모델 가중치를 자동으로 다운로드하지 않는다.** 원하는 모델의 GGUF 파일을 먼저 준비하고 `settings.yaml`의 `MODEL_GGUF_PATH`를 설정한다. 공개 GGUF 모델은 Ollama 클라이언트 없이 Hugging Face CLI로 다운로드할 수 있다. 공식 사용법: https://huggingface.co/docs/huggingface_hub/en/guides/cli

프로젝트 루트의 PowerShell에서 실행한다.

```powershell
# 다운로드용 CLI 설치 (기존 Portable 실행에 필수인 도구는 아님)
python -m pip install --upgrade huggingface_hub
hf download --help

# 실제 Hugging Face 모델 페이지의 Files 목록에서 저장소 ID와 파일명을 확인한다.
# 다음 OWNER/REPO, MODEL-Q4_K_M.gguf는 예시 자리표시자이므로 실제 값으로 교체한다.
hf download OWNER/REPO MODEL-Q4_K_M.gguf --local-dir "./portable/portable-models/gguf"
```

원하는 파일이 `portable/portable-models/gguf/MODEL-Q4_K_M.gguf`로 다운로드됐다면 `settings.yaml`에 **실제 다운로드한 파일명**을 지정한다.

```yaml
MODEL_GGUF_PATH: "portable/portable-models/gguf/MODEL-Q4_K_M.gguf"
```

분할 GGUF는 모든 파트가 필요하며, 이미지 모델은 호환되는 `mmproj` 등 추가 파일이 필요할 수 있다. 공개되지 않았거나 동의가 필요한 모델은 해당 모델의 라이선스·인증을 먼저 확인해야 한다. 특정 버전을 재현하려면 `hf download`에 `--revision`으로 저장소 커밋 SHA를 지정할 수 있다. 파일의 GGUF 헤더만으로 모델 종류·호환성·안전성이 확인되지는 않는다.

### 대안: Ollama에 이미 설치된 모델 복사

Ollama가 있다면 다음 명령으로 `FROM`이 가리키는 blob 경로를 확인한다.

```powershell
ollama show gemma3:12b --modelfile
```

`sha256-...` 이름의 파일이 **정말 GGUF 모델 본체인지** 파일 헤더와 크기·메타데이터를 확인한 후 `portable/portable-models/gguf/`에 원하는 `.gguf` 이름으로 **복사**한다. Ollama blob에는 템플릿·projector 등 본체 이외의 데이터가 포함될 수 있고, Ollama 템플릿이 llama.cpp에 자동 이식되는 것은 아니다. 원본 blob은 삭제하지 않는다.

## 자동설치 스크립트가 다운로드하는 항목과 출처

`python portable/resolve_portable_sources.py`는 GitHub 릴리스 메타데이터에서 **공식 배포 URL과 제공자 선언 SHA-256**을 읽어 `portable/portable-sources.json`을 만든다. 이 단계는 실행파일이나 GGUF를 다운로드하지 않으며 기존 매니페스트는 자동 덮어쓰지 않는다.

| 항목 | 공식 제공처 | 목적 및 내려받는 파일 |
|---|---|---|
| Portable Python | `astral-sh/python-build-standalone` | Windows x64 CPython 3.11 `install_only.tar.gz` |
| uv | `astral-sh/uv` | Windows x64 `uv-x86_64-pc-windows-msvc.zip` |
| llama.cpp | `ggml-org/llama.cpp` | Windows x64 Vulkan `llama-*-bin-win-vulkan-x64.zip` |

`prepare-portable.ps1`이 매니페스트에 고정된 세 압축파일을 `portable/portable-downloads/`에 다운로드하고 SHA-256을 검사한 뒤 `portable/portable-runtimes/`에 설치한다. 선택된 **정확한 버전·URL·SHA-256**은 다운로드 전에 다음 명령으로 확인할 수 있다.

```powershell
Get-Content ./portable/portable-sources.json -Raw
Get-Content ./pyproject.toml
```

이후 Portable Python에 `uv pip install --python <Portable Python> <프로젝트 경로>`로 MAI 및 다음 **직접 의존성 전체**와 각 패키지의 하위 의존성을 설치한다. `pyproject.toml`에서 변경 여부를 확인할 수 있다.

- 웹·API: `markdown-it-py`, `beautifulsoup4`(`bs4`), `ddgs`, `fastapi`, `httpx`, `pydantic`, `python-dotenv`, `PyYAML`, `python-multipart`, `uvicorn`
- 브라우저: `playwright`
- 문서: `openpyxl`, `pypdf`, `python-docx`, `python-pptx`
- 음성·연산: `speakeronnx`, `vadonnx`, `scipy`, `faster-whisper`, `numpy`
- 코드 분석: `tree-sitter`, `tree-sitter-javascript`, `tree-sitter-typescript`, `tree-sitter-c`, `tree-sitter-cpp`, `tree-sitter-java`, `tree-sitter-go`, `tree-sitter-rust`, `tree-sitter-ruby`
- Git 직접 설치: `sentence-breaker @ git+https://github.com/FoggyCaligo/Sentence_Breaker.git@main`

`pytest`, `jsonschema`는 선택적인 `[dev]` 개발 의존성이며 기본 설치 항목은 아니다. Python 패키지와 하위 의존성 전체를 별도 해시 잠금파일로 고정한 구조는 아니다. 특히 `sentence-breaker`는 변경 가능한 `main`을 참조한다. **세 런타임 파일의 SHA 검증이 모든 Python 의존성을 검증한다는 뜻은 아니다.**

Playwright Python 패키지는 위 기본 의존성에 포함되지만, **Chromium 브라우저 실행파일은 기본 명령으로 설치되지 않는다.** `-InstallChromium`을 지정한 경우에만 `python -m playwright install chromium`을 추가 실행하여 `portable/portable-runtimes/playwright-browsers/`에 브라우저 바이너리를 다운로드한다.

자동설치로 **내려받지 않는 항목:** GGUF 메인/비전/Verifier 모델 및 projector, Ollama 클라이언트, Tailscale, cloudflared, GPU 드라이버, Hugging Face CLI. `-ModelFile`은 모델을 다운로드하는 옵션이 아니라 **이미 있는 GGUF를 복사하는 옵션**이다.

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

**KV 캐시 범위:** `RoleCacheRun` 체크포인트 파일명에 GGUF 경로·크기·수정 시각으로 생성한 모델 식별 네임스페이스를 추가했다. 메인/숫자/최종 검증 역할은 각자 별도 체크포인트이며 모델이 다르면 서로 재사용하지 않는다. **이는 모델 교체 인프라의 첫 단계일 뿐**, 현재 `dev2`는 하나의 llama.cpp 모델만 적재하며 LFM 등 별도 Verifier 모델을 VRAM에서 교대로 불러오는 기능은 구현하지 않았다. 파일명 격리만으로 교차 모델 캐시 복원이 안전해지는 것이 아니다.

## llama.cpp 이미지 분석 (복원됨)

dev2는 Ollama를 사용하지 않고, **현재 적재된 llama.cpp GGUF**가 멀티모달 이미지 입력을 지원할 때만 이미지를 분석한다.
- 사용자 채팅에 첨부한 PNG/JPEG/GIF/WEBP 이미지는 계정별 업로드 경로를 확인한 뒤, llama.cpp의 OpenAI 호환 `image_url` 데이터 URI로 전달한다.
- 별도의 `image_analyze` 도구도 복원되었다. 이 도구는 실제 파일 경로와 질문을 받아, **현재 활성 모델**을 통해 이미지를 분석한다. Owner는 접근 가능한 로컬 파일을 지정할 수 있으며, Trial 도구는 해당 계정의 업로드 경로로 한정된다.
- 모델이 이미지 입력을 지원하려면 해당 llama.cpp 빌드·채팅 템플릿·모델에서 멀티모달 처리가 지원되고 필요한 projector(예: `mmproj`)가 함께 적재되어야 한다. 텍스트 전용 GGUF로 이미지를 분석할 수는 없으며 이때 런타임 오류를 숨기거나 가짜 묘사를 반환하지 않는다.
- Windows Portable: `powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/start-portable.ps1 -MmprojPath 'portable/portable-models/gguf/vision-mmproj.gguf'` (실제 projector가 있는 경우)
- Linux Portable: `MAI_MMPROJ_PATH=portable/portable-models/gguf/vision-mmproj.gguf ./portable/start-portable-linux.sh` (실제 projector가 있는 경우)
- projector 파일은 자동 다운로드하거나 임의의 모델에 붙이지 않는다. 선택한 본체 GGUF에 맞는 파일을 사용해야 한다.
- **별도 Vision 모델의 교대적재는 아직 미구현**이다. 텍스트 전용 Main + 별도 Vision GGUF를 번갈아 사용하려면 향후 단일 모델 적재 관리자를 추가해야 한다. 이 복원은 두 모델을 동시에 VRAM에 올리지 않는다.

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
이미지 첨부 직접 인식은 현재 단일 모델 llama.cpp 경로에서 지원하지 않는다.
가중치 적재 여부, VRAM·RAM, GPU 전원 안정성은 실제 컴퓨터에서 검증해야 한다.

## 테스트

```bash
python -m pytest -q
```

기존 기록의 `602 passed`는 이 구조 변경 이전 결과다.
새 구조의 테스트는 별도로 실행해 확인해야 한다.

추가 지침은 [Portable 안내](portable/README.md)를 참고한다.
