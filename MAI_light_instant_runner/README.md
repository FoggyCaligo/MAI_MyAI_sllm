# MAI Light Instant Runner — 로컬 포트폴리오 배포

이 배포판은 **MAI.exe를 한 번 실행하면 기존 MAI 채팅 UI가 자동으로 열리는** Windows 로컬 전용 데모다.

- **로그인 불필요:** 계정 ID, 비밀번호, `.env` 파일 입력 없음. 내부 사용자는 `portfolio-local`로 고정한다.
- **기존 MAI 화면 유지:** `mai/app/static/index.html` 원본을 가져와 배포 복사본에서만 로그인 화면과 원격접속 버튼을 숨긴다.
- **외부 접속 차단:** 포트폴리오 서버는 `127.0.0.1:8000`에서만 응답하고, 원격 워커·Tailscale·계정 서버 코드는 배포본에 포함하지 않는다.
- **자동 실행:** `MAI.exe` 시작 시 Portable 서버를 실행하며, `/health`가 로컬 데모임을 확인하면 웹 브라우저로 기존 채팅 UI를 자동으로 연다.
- **민감정보 미포함:** 원본 `.env`, 사용자 DB/기억/업로드와 로그인 토큰은 ZIP에 복사하지 않는다. 공유 계정과 비밀번호도 생성하지 않는다.

```text
MAI_light_instant_runner/
├── build.ps1
├── portfolio_server.py
├── portfolio-mai.ps1
├── README.md
├── launcher/
│   ├── Program.cs
│   └── MAI.Launcher.csproj
└── MAI/                 # 생성된 포트폴리오 배포본 (ZIP 압축 대상)
    ├── MAI.exe
    ├── mai.ps1          # 포트폴리오용 로컬 실행 스크립트
    ├── scripts/portfolio_server.py
    ├── mai/             # 실제 에이전트 코어 + 기존 채팅 UI에서 필요한 부분만
    ├── portable/        # Portable Python/llama.cpp; 옵션에 따라 포함
    └── settings.yaml
```

## 제작

Windows의 PowerShell에서 .NET 8 SDK를 설치하고, 이 저장소 루트에서 실행한다.

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\MAI_light_instant_runner\build.ps1 -IncludeRuntime -IncludeModels
```

완료되면 **`MAI_light_instant_runner/MAI/` 폴더 자체의 내용 전체를 ZIP으로 압축**한다. 수신자는 압축을 해제한 뒤 `MAI.exe`만 실행한다. MAI.exe 단독으로 파일 하나만 복사하면 Python과 llama.cpp 및 GGUF가 없어 실행할 수 없다.

빌드 스크립트는 기존 `MAI/` 결과물이 있으면 덮어쓰지 않고 중단한다. 새 빌드 전에 이전 결과물을 백업하거나 다른 곳으로 이동한다.

## 실제 기능 범위

이 배포판은 MAI **로컬 데모**다. 대화, llama.cpp GGUF 추론, 메모리 검색과 답변 후 기억 추출, 계산/시간 도구 등을 포함한다. 운영 버전의 계정/로그인, 외부 연결, 원격 PC 작업, 웹 검색 및 파일 편집 등의 기능은 포함하지 않는다. 기존 UI의 일부 버튼은 숨겨질 수 있다.

`.env.example`을 `.env`로 복사해 자동 로그인을 시도하지 않는다. 사용자에게 배포된 비밀번호는 비밀이 아니며, 로그인 서버를 포함하지 않는 것이 이 로컬 데모에 더 적합하기 때문이다.

주의: 로컬에서 별도 프로그램이 포트 8000을 선점하거나, GGUF 모델이나 Portable 런타임이 누락되면 자동 실행이 실패할 수 있다. 실행기 로그에서 원인을 확인할 수 있다. EXE 코드 서명 및 실제 수신 PC의 GPU 구동 검증은 추가로 필요하다.
