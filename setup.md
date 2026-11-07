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
외부 공개가 필요 없다면 `TAILSCALE_FUNNEL=false`로 설정한다.
비밀번호, 개인 DB, API 토큰은 Git에 커밋하지 않는다.

## 모델

`settings.yaml`에서 모델 관련 값은 다음 하나다.

```yaml
MAIN_MODEL: "mai-model"
```

이 값은 **실제 GGUF 파일 이름이 아닌 llama.cpp 모델 alias**다.
실행할 GGUF 파일은 시작 스크립트의 `-GgufPath` 또는
`portable/portable-models/gguf/active.gguf`로 지정한다.
한 번에 모델 한 개만 올린다.

## Windows Portable 설치

```powershell
python portable/resolve_portable_sources.py
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/prepare-portable.ps1 -Manifest ./portable/portable-sources.json -ModelFile "D:\\models\\my-model.gguf"
powershell -NoProfile -ExecutionPolicy Bypass -File ./portable/start-portable.ps1
```

매니페스트에는 공식 릴리스의 특정 Python·uv·llama.cpp Vulkan
바이너리와 제공자가 선언한 SHA-256이 포함된다.
내부 Runtime 디렉터리는 모델 파일과 분리되므로
새 llama.cpp 릴리스로 교체해도 GGUF와 DB는 유지된다.
기존 엔진은 `-PreserveAndReinstallExisting`로 백업 후 교체할 수 있다.

서버는 `http://127.0.0.1:8000`에서 접근한다.

## 실행 제약

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
