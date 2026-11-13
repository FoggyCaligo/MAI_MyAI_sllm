# MAI Windows EXE 배포 디렉터리

`./MAI_light_instant_runner/build.ps1`은 **현재 저장소의 코드를 수정하지 않고** 아래 폴더를 생성합니다.

```text
MAI_light_instant_runner/
├── build.ps1                  # 배포 구성 스크립트
├── launcher/
│   ├── MAI.Launcher.csproj    # .NET 8 Windows Forms 실행기
│   └── Program.cs
└── MAI/                       # 빌드 후 결과물
    ├── MAI.exe                # 시작/종료/재시작/검사/설치/업데이트
    ├── mai.ps1
    ├── mai.sh
    ├── run_server.py
    ├── settings.yaml
    ├── .env.example
    ├── mai/                   # 에이전트 + MCP 관리
    │   ├── mai_uploads/       # 기본 빈 폴더 (개인 데이터는 복사하지 않음)
    │   └── mcp_tools/
    │       └── mcp_memorizer/
    │           └── MAI_MEMORY/  # 기본 빈 폴더
    └── portable/
        ├── portable-runtimes/ # -IncludeRuntime 시 복사
        └── portable-models/   # -IncludeModels 시 복사
```

## 준비 및 빌드

Windows PowerShell에서 **.NET 8 SDK** 및 Git을 준비합니다.

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\MAI_light_instant_runner\build.ps1
# 기기에 이미 검증된 Portable Python/llama.cpp 런타임이 있으면:
.\MAI_light_instant_runner\build.ps1 -IncludeRuntime
# GGUF 모델까지 포함하려면:
.\MAI_light_instant_runner\build.ps1 -IncludeRuntime -IncludeModels
```

빌드 성공 후 `MAI_light_instant_runner/MAI/MAI.exe`를 실행합니다. GUI 버튼은 원래
`mai.ps1`의 start/end/restart/check/setup/update 명령을 호출하므로
서버의 동작 방식, MCP 연결 규칙, GGUF 모델 선택 방법을 변경하지 않습니다.

**중요:** 코드와 빈 폴더만 묶는 기본 빌드는 실행 가능한 완전 설치본이
아닙니다. `Start`를 사용하려면 `setup`으로 필요한 런타임을 설치하고
GGUF 모델을 준비해야 합니다. `-IncludeRuntime`을 사용하면 Portable Python
패키지의 editable 경로가 원본 저장소를 가리킬 수 있으므로 배포 복사본에서
의존성을 다시 연결합니다. 빌드 스크립트는 이를 설치 단계로 수행하며, 실패하면
배포 생성을 중단합니다. 모델은 반드시 라이선스를 확인하고 배포하세요.

**개인정보:** `.env`, `MAI_MEMORY`, `mai_uploads`, 인증서/토큰,
프로세스 로그, MCP 비공개 설정은 **복사하지 않습니다**. 필요하면 서버를 종료한
뒤 개인 환경에서 별도로 백업 및 이동하세요. 배포물을 공개하기 전에 반드시
내용을 검사하세요.

**한계:** .NET 실행기는 EXE이지만 내부 MAI 서버, MCP 서버와 llama.cpp는
기존 Portable Python 및 독립 실행 파일을 계속 사용합니다.
Windows 전용이며 EXE 서명, Smart App Control, 백신 오탐, Windows 실제 GPU
실행 검증은 별도로 필요합니다.
