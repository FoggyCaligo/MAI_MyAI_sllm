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

PowerShell에서 다음으로 확인한다.

```powershell
# 실제 GGUF 경로를 지정한다. 경로의 공백과 한글은 따옴표로 감싼다.
$model = 'F:\\모델\\실제모델.gguf'
Test-Path -LiteralPath $model -PathType Leaf
# True일 때만 다음 명령을 실행
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/prepare-portable.ps1 -Manifest ./portable/portable-sources.json -ModelFile $model
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/start-portable.ps1
```

모델이 이미 외장 드라이브의 `portable/portable-models/gguf/active.gguf`에 존재한다면 `-ModelFile` 옵션을 생략하거나, 외부 경로를 직접 지정하여 `start-portable.ps1 -GgufPath 'F:\\모델\\실제모델.gguf'`로 시작할 수 있다.

Ollama의 `sha256-...` 파일은 Blob 저장소 항목이므로 **확장자가 없다는 이유만으로 GGUF가 아니라고 단정할 수 없으며**, 반대로 모두 GGUF인 것도 아니다. `ollama show 모델명 --modelfile`로 `FROM`이 가리키는 파일을 확인하고 처음 4바이트가 `GGUF`인지 검사한 뒤, 해당 모델 데이터 파일만 정확히 선택하여 복사해야 한다. 템플릿이나 부가 설정은 GGUF 파일에 포함되지 않을 수 있다.

`start-portable.ps1`은 YAML 경로를 별도 `portable/resolve_model_path.py`에서 읽는다. 경로가 없으면 모델 파일 위치를 분명히 알려주는 오류를 출력한다. 이미 설치한 Python/uv/llama.cpp를 모델 경로 오류만으로 새로 다운로드할 필요는 없다.

## Windows Portable 설치

```powershell
python portable/resolve_portable_sources.py
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/prepare-portable.ps1 -Manifest ./portable/portable-sources.json -ModelFile "D:\\models\\실제모델.gguf"
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/start-portable.ps1
```

매니페스트에는 공식 릴리스의 특정 Python·uv·llama.cpp Vulkan
바이너리와 제공자가 선언한 SHA-256이 포함된다.
내부 Runtime 디렉터리는 모델 파일과 분리되므로
새 llama.cpp 릴리스로 교체해도 GGUF와 DB는 유지된다.
기존 엔진은 `-PreserveAndReinstallExisting`로 백업 후 교체할 수 있다.

서버는 `http://127.0.0.1:8000`에서 접근한다. 위의 모델 경로는 **실제 보유한 GGUF 파일로 교체**해야 한다. `sha256-...` 파일은 Ollama 저장소의 Blob일 수 있으므로, GGUF 헤더가 있는 모델 데이터인지 먼저 확인한 뒤 복사해야 한다. 단일 GGUF 파일이 아닌 모델 구성은 별도로 확인한다.

## 원격 웹 호스팅: Tailscale(기본) / Cloudflare

```yaml
REMOTE_HOSTING_PROVIDER: tailscale     # tailscale | cloudflare | none
```

Tailscale Funnel은 호스트에 Tailscale을 설치·로그인하고 Funnel 권한을 허용해야 공개된다. `cloudflare`를 선택하려면 호스트에 cloudflared를 설치하고, Cloudflare Dashboard에서 관리형 Named Tunnel 및 `http://127.0.0.1:8000`으로 연결할 공개 호스트명을 만든 후 `.env`에 `CLOUDFLARE_TUNNEL_TOKEN`을 지정한다. `none`은 이번 MAI 프로세스의 원격 공개 실행을 건너뛴다.

기존 `TAILSCALE_FUNNEL`은 사용하지 않는 구형 설정이므로 `.env`에 남아 있다면 삭제할 수 있다. Tailscale Funnel은 MAI 종료 이후에도 유지될 수 있으므로 다른 제공자로 전환 시 `tailscale funnel status`로 기존 공개 경로를 확인하고 필요한 경우 명시적으로 해제한다. 자세한 내용은 [한국어 원격 호스팅 설정](docs/remote-hosting.md)을 참고한다.

호스팅과 원격 PC 명령 실행 세션은 별개다. 원격 PC 연결 시간 정책은 각 브랜치의 원격 세션 코드를 따른다.

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
