# MAI 외장 SSD Portable 설정 안내

> **이 프로젝트의 Windows·Linux 공용 Portable 구성용 외장 SSD는 NTFS로 준비함.**
> 이 규칙은 MAI Portable의 지원 대상 파일시스템을 고정하기 위한 프로젝트 요구조건임. Linux 자체가 NTFS만 지원하거나 NTFS라면 모든 Linux 실행 조건이 자동 해결된다는 뜻은 아님.

## 1. 준비 전 확인

- 현재 Windows Portable 구성은 `prepare-portable.ps1`, `start-portable.ps1`로 준비함.
- **Linux 실행 진입점과 Linux 런타임 준비 자동화는 아직 구현되지 않았음.** NTFS 포맷만으로 Linux에서 MAI가 실행되는 것은 아님.
- SSD를 포맷하면 기존 파일이 지워짐. 먼저 다른 물리 디스크(HDD 등)에 **MAI 전체 폴더와 사용자 설정·DB·모델·다운로드 원본**을 복사할 것. 복사 완료 후 백업을 열어 파일이 실제로 존재하는지 확인할 것.
- **설치 스크립트 또는 MAI가 실행 중이라면 먼저 종료할 것.** PowerShell·Ollama·Python 등 SSD를 사용하는 프로세스가 완전히 종료됐는지 확인할 것.
- 포맷 대상이 외장 SSD가 맞는지 **드라이브 문자뿐 아니라 용량과 드라이브 이름**을 확인할 것. 백업 HDD를 잘못 선택하지 말 것.

## 2. Windows에서 외장 SSD를 NTFS로 포맷

1. 파일 탐색기 → **내 PC**를 열고 외장 SSD를 찾음.
2. 외장 SSD를 마우스 오른쪽 버튼으로 눌러 **속성**에서 기존 파일시스템(exFAT 등), 드라이브 이름, 용량을 확인함.
3. 백업을 다른 물리 디스크에 완료한 뒤 외장 SSD를 오른쪽 버튼으로 눌러 **포맷**을 선택함.
4. **파일 시스템: NTFS**를 선택함. 다른 드라이브를 선택하지 않았는지 다시 확인하고 포맷함.
5. 포맷 완료 후 **속성 → 파일 시스템: NTFS**를 확인함.
6. 외장 HDD에 보관한 MAI 폴더 전체를 SSD로 복사함. 숨김 파일 `.env`, `data/`, `portable-models/`, `portable-runtimes/`, `portable-downloads/`, `portable-sources.json` 등을 빠뜨리지 말 것.
7. Windows Portable 설치가 이미 완료됐다면 `start-portable.ps1`로 실행·종료까지 확인함. 설치가 중단돼 있었다면 [Windows Portable 상세 가이드](docs/portable-windows.md)를 참고해 완료함.

**포맷 실행을 MAI 설치 스크립트에서 자동 수행하지 않음.** 데이터 삭제와 디스크 오선택 위험이 있으므로 사용자가 직접 수행함.

## 3. Windows·Linux 공용 저장소 운영 원칙

- **공유 대상:** MAI 소스코드, 개인 설정, SQLite 대화·기억·로그 DB, Ollama 모델 저장소 및 GGUF 모델.
- **분리 대상:** 운영체제별 Python 바이너리·네이티브 패키지, Ollama·llama.cpp 바이너리, Playwright 브라우저. Windows 실행 파일을 Linux에서 직접 실행하지 않음.
- 현재 Windows 런타임은 `portable-runtimes/python/`, `portable-runtimes/ollama/`, `portable-runtimes/llamacpp/`에 설치됨. **Linux용 디렉터리 구조와 시작 명령은 추후 구현 시 결정하며, 현재 경로를 임의로 변경하지 말 것.**
- Windows·Linux에서 **동일 SQLite DB를 동시에 열어 쓰지 말 것**. 운영체제 전환 전 MAI 서버와 DB 접근 프로세스를 종료하고 SSD를 안전하게 제거함.
- NTFS는 Linux의 NTFS 드라이버 및 마운트 옵션(`uid`, `gid`, `fmask`, `dmask`, `noexec` 등)에 영향을 받음. 따라서 NTFS라고 해서 Linux 실행 권한·심볼릭 링크·Python 종속 패키지·GPU 드라이버 문제가 자동 해결되지는 않음.
- 외장 SSD의 위치는 Windows 드라이브 문자, Linux 마운트 위치가 달라질 수 있으므로 실행 스크립트는 자신의 위치를 기준으로 공통 데이터 경로를 찾아야 함.
- Ubuntu 등의 Linux에서 실행하려면 호스트 CPU 아키텍처, glibc 등 시스템 라이브러리, GPU 드라이버, 파일시스템 마운트 조건, 모델 호환성을 검증해야 함. **현재 Linux에서 'SSD 연결 즉시 실행'은 미지원·미검증 상태임.**

## 4. 외장 HDD 백업

1. MAI와 Ollama 등을 완전히 종료함.
2. 외장 SSD의 MAI 폴더를 다른 물리 디스크인 외장 HDD에 **날짜별 폴더로 전체 복사**함.
3. 백업에 `.env`, DB, 모델, 원본 아카이브가 포함됐는지 확인함.
4. 가능하면 [백업 검증 스크립트](docs/portable-windows.md)로 스냅샷을 검증함. 단순 복사본은 해시 검증까지 자동 보장하지 않음.
5. `.env`와 DB에는 개인정보·인증정보가 있을 수 있으므로 백업 HDD의 암호화 및 접근 통제를 권장함.

## 5. Linux 지원 완료 전 체크리스트

- [ ] SSD 파일시스템 NTFS 확인
- [ ] Windows MAI 정상 실행 및 종료 확인
- [ ] Linux용 네이티브 실행 환경 준비 기능 구현
- [ ] Linux 실행 파일/라이브러리의 저장 방식과 NTFS 마운트 제약 검증
- [ ] 같은 데이터·모델을 이용한 Linux 실행 테스트
- [ ] Windows → Linux → Windows 전환 후 DB·모델 무결성 테스트
- [ ] 외장 HDD 전체 백업 및 복구 테스트

Windows Portable 상세 명령은 [docs/portable-windows.md](docs/portable-windows.md) 참조.
