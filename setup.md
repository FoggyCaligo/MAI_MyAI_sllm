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
이미지 첨부 직접 인식은 현재 단일 모델 llama.cpp 경로에서 지원하지 않는다.
가중치 적재 여부, VRAM·RAM, GPU 전원 안정성은 실제 컴퓨터에서 검증해야 한다.

## 테스트

```bash
python -m pytest -q
```

기존 기록의 `602 passed`는 이 구조 변경 이전 결과다.
새 구조의 테스트는 별도로 실행해 확인해야 한다.

추가 지침은 [Portable 안내](portable/README.md)를 참고한다.
