## 배포 파일 URL 및 SHA-256 자동 생성

참고: llama.cpp의 GitHub `/releases/latest`에는 Windows용 바이너리가 없을 수 있어, resolver는 최근 공식 릴리스 목록에서 Windows x64 Vulkan 바이너리가 있는 가장 새로운 릴리스를 찾음. 선택한 릴리스 태그와 파일명을 출력하고 배포자가 제공한 SHA-256이 없으면 실패함.


기존 portable-sources.example.json에는 실제 다운로드 정보 대신 자리표시자가 있었음. 먼저 아래 명령을 실행해 공식 GitHub 릴리스 API의 배포자 제공 SHA-256과 URL을 portable-sources.json에 고정함.

```bash
python scripts/resolve_portable_sources.py
```

llama.cpp Vulkan 릴리스까지 포함하려면 `--include-llamacpp` 옵션을 추가함. SHA-256이 제공되지 않거나 지정된 자산이 없으면 오류로 종료함. 기존 설정 파일은 덮어쓰지 않음.

이 명령은 PC에 이미 설치된 Python으로 최초 한 번 실행함. 생성 후 prepare-portable.ps1을 실행할 것. 최초 다운로드는 인터넷이 필요하며, 오프라인 장기 보관을 위해 설치된 모델과 원본 아카이브를 별도 백업해야 함.

---
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

## Portable 실행 환경 자동 준비 (첫 PC에서 1회 실행)

\`prepare-portable.ps1\`가 이동 가능한 Python 런타임, 패키지 설치 도구 uv, Ollama 독립 CLI, 선택형 llama.cpp를 준비하고 모델과 Chromium을 외장 SSD에 복사/다운로드함. **이 스크립트는 실행 파일을 자동으로 설치하되 배포처와 해시를 임의로 추측하지 않음.** 먼저 정확한 배포 파일을 고정해야 함.

1. 외장 SSD에 이 저장소를 복사하고 Windows PowerShell을 실행함.
2. \`portable-sources.example.json\`을 \`portable-sources.json\`으로 복사함. 공식 배포 파일의 **정확한 HTTPS URL과 SHA-256**을 채움. 값이 미지정이면 설치는 실패함. Python 항목에는 **Windows x64용 python-build-standalone의 install_only 아카이브**를 사용해야 함. 공식 CPython embeddable ZIP은 pip 기반 패키지 설치를 지원하지 않으므로 여기서는 권장하지 않음.
3. Ollama Windows **standalone CLI ZIP**과 llama.cpp 공식 Windows Vulkan ZIP, uv의 Windows x64 ZIP 파일을 지정함. 특히 Ollama에 AMD ROCm DLL이 추가로 필요한 경우 추가 공식 아카이브의 수동 통합이 필요할 수 있음. 모델 실행 가능성을 실제로 검증할 것.
4. 기존 PC 모델 디렉터리를 복사하려면 Ollama 서비스를 종료한 후 다음 명령을 실행함.

\`\`\`powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\prepare-portable.ps1 -Manifest .\portable-sources.json -CopyLocalModels -InstallChromium
\`\`\`

새로 공식 Ollama 라이브러리에서 모델을 받으려면 기존 모델 복사 대신 아래 명령을 실행함. \`portable-sources.json\`의 \`models\` 항목에 명시한 정확한 모델명(기본 예시 \`ornith-1.5:9b\`, \`gemma4:e4b\`)만 요청함. 모델이 더 이상 제공되지 않으면 실패함.

\`\`\`powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\prepare-portable.ps1 -Manifest .\portable-sources.json -PullModels -InstallChromium
\`\`\`

완료 후 \`start-portable.ps1\` 실행. 실행 환경과 PC의 GPU·드라이버 호환성, Windows DLL, Playwright, Sentence Breaker 등 타사 종속성의 완전 이동성은 새 PC에서 별도 검증해야 함. **현재 자동 준비 코드와 실행 스크립트는 실제 Windows PC에서 끝까지 테스트되지 않았음.**

- \`portable-sources.json\`을 Git에 커밋하지 말 것(버전 기록이 필요하면 검증된 해시만 별도 보관). 모델 파일과 추론 엔진의 **오프라인 백업이 다운로드 지속성의 유일하게 현실적인 대안**임.
- 다운로드한 원본 ZIP/TAR는 \`portable-downloads/\`에 저장되므로 외장 SSD와 백업 디스크에 함께 남길 것.
- 재실행은 이미 설치된 런타임 폴더를 덮어쓰지 않고 오류로 중단함. 업데이트는 기존 세트를 백업한 뒤 새 Portable 복제본에서 수행할 것.
- \`-CopyLocalModels\` 실행 전 소스의 \`OLLAMA_MODELS\` 환경변수 또는 \`%USERPROFILE%\.ollama\models\` 경로를 확인할 것.
- 이 절차는 **준비 자동화**이며 정기 백업 예약의 자동 생성과는 별도임. 백업은 기존 \`scripts/portable_backup.py\` 및 \`scripts/verify_portable_backup.py\` 사용.
