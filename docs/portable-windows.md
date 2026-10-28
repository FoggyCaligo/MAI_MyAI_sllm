# MAI Portable (Windows x64)

목표: 외장 SSD에 MAI 소스·개인 설정·SQLite DB·모델 가중치·실행 도구를 모아 다른 Windows x64 PC에서도 드라이브 문자와 관계없이 실행함. **현재 이식된 runtime 자체는 자동 공급되지 않음.** 실행 파일과 모델이 실제로 준비되지 않으면 명확하게 실패함.

## 필수 외장 SSD 배치

```text
MAI/
  start-portable.ps1
  run_server.py
  .env                         # 개인 로그인 자격 증명 포함, Git 미추적
  mai/
  data/
  mai_uploads/
  portable-models/ollama/      # 기존 Ollama models 디렉터리 내용
  portable-models/gguf/        # llama.cpp용 독립 GGUF (선택)
  portable-runtimes/python/python.exe    # 이동 가능한 Python + 설치된 MAI 종속 패키지
  portable-runtimes/ollama/ollama.exe
  portable-runtimes/llamacpp/llama-server.exe  # 선택
  portable-runtimes/playwright-browsers/       # Playwright Chromium (필요 시)
```

- 현재 PC의 `%USERPROFILE%\.ollama\models` **내용 전체**를 `portable-models/ollama`에 복사함. 이 작업에는 `blobs`, `manifests` 및 기타 메타데이터가 포함됨. 서비스가 작동 중이면 먼저 종료하고 복사할 것.
- Ollama 공식 설치본의 실제 배포 파일·동적 라이브러리와 호환되는 폴더를 `portable-runtimes/ollama`에 준비해야 함. `ollama.exe` 단독 복사가 충분하다고 보장하지 않음.
- Python은 자체 이동 가능한 Windows 배포판을 준비하고 해당 환경의 `python.exe -c "import uvicorn, ollama, fastapi"` 등으로 실제 필요한 패키지가 사용 가능한지 확인함. **기존 `.venv`를 단순 이동시키지 말 것.** 실행 스크립트가 시스템 Python으로 조용히 대체하지 않음.
- `portable-runtimes/playwright-browsers`는 Chromium 사용 시 필요함. 플랫폼별 필요한 드라이버·런타임과 Windows OS 기능은 대상 PC에 별도로 필요할 수 있음.
- 추가적인 MAI 외부 리소스(예: 음성 모델, Google OAuth 인증 파일, Tailscale 구성)는 사용 중인 기능에 따라 추가 조치가 필요하며 현재 launcher가 전부 패키징하지는 않음.

## 실행

PowerShell에서 외장 SSD의 `MAI` 폴더로 이동한 다음:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-portable.ps1 -Backend ollama
```

llama.cpp는 PR #324의 선택형 백엔드가 `dev`에 반영되어 있어야 하며, 별도 GGUF 모델과 호환 채팅 템플릿이 필요함:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-portable.ps1 -Backend llamacpp -GgufPath .\portable-models\gguf\model.gguf -ModelAlias local-model
```

서버와 모델의 실행은 **검증된 고유 포트**를 사용해야 함. 해당 포트에 다른 서버가 먼저 떠 있다면 잘못된 모델을 이용하지 않도록 포트 점유 확인이 필요함. 첫 이동 후에는 모델 목록, THINK, 메모리 검색, Verifier, 대화 기록과 도구를 직접 시험할 것.

## 백업(매주 토요일 권장)

**다른 물리 디스크**에 백업해야 함. 백업 드라이브 문자가 변경될 수 있으므로 Windows 디스크 관리에서 주기적으로 사용할 고정 문자를 할당하거나 작업 스케줄러 실행 명령을 관리할 것.

MAI 종료 후, 외장 SSD 내 준비된 Python으로 실행함:

```powershell
.\portable-runtimes\python\python.exe scripts\portable_backup.py --source . --destination X:\MAI-Backups
```

백업은 날짜별 별도 스냅샷을 만들고 SQLite 파일에 Python backup API를 사용하며, 파일별 SHA-256 목록을 생성함. 개인 `.env`, 대화·기억·로그 DB가 백업에 포함되므로 **백업 디스크를 암호화하고 접근 권한을 제한할 것.** 백업 대상 폴더와 원본 폴더가 분리되어야 함. 원본 프로젝트가 실행 중인 상태의 전체 파일 일관성은 보장되지 않으므로 MAI를 종료 후 백업할 것.

```powershell
.\portable-runtimes\python\python.exe scripts\verify_portable_backup.py X:\MAI-Backups\mai-YYYYMMDDTHHMMSSZ
```

작업 스케줄러를 사용하려면 **MAI가 실행 중이 아닐 때** 토요일에 위 백업 명령을 수행하도록 등록해야 함. 자동 등록은 백업 드라이브 경로 및 실행 시각이 지정되지 않았으므로 이 PR에서 수행하지 않음. PC 전원이 꺼져 있거나 백업 디스크가 없으면 예약 실행이 실패할 수 있으므로 작업 스케줄러의 실패 이력과 주기별 스냅샷을 확인할 것.

## 복구

1. 백업 스냅샷을 검증하고 내용 전체를 새 외장 SSD의 MAI 디렉터리로 복사함(백업 manifest는 프로젝트에서 제거해도 됨).
2. 드라이브 문자 변경 이후 `start-portable.ps1`로 실행함.
3. Python 및 Ollama/llama.cpp 바이너리의 새 PC 아키텍처, GPU 가속 지원 여부 확인. GPU 드라이버 등은 대상 PC에 설치 필요.
4. 모델/THINK/툴 호출/메모리/대화 이력을 검사함.

**보장 범위:** 이 PR은 Windows용 상대경로 실행 진입점과 일관된 백업·검증 코드를 제공하는 단계임. 실행 파일의 공식 릴리스 자동 다운로드·Python dependency wheelhouse·완전 오프라인 신규 PC E2E 테스트는 아직 구현되지 않았고, 현재 모델/런타임이 저장소만으로 자동 복원되지는 않음. 런타임/모델 장기 보존에 대한 별도 안내는 [llama.cpp 문서](llamacpp-portability.md)를 참고할 것.
