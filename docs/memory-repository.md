# 독립 Memory Git 저장소 연결 및 데이터 이전

MAI는 개인 데이터베이스를 본체 코드 저장소와 분리한다. 기본 데이터 저장소 경로는
프로젝트 루트의 `MAI_MEMORY/`다. 이 폴더는 별도 Git 저장소이고
MAI 본체의 `.gitignore`로 추적하지 않는다.

## 안전 수칙

- **MAI와 원격 워커 등 관련 프로그램을 종료한 상태**에서 기존 데이터 이전 및 Git 커밋을 수행한다.
- Memory 저장소는 개인 대화·장기기억·로그 등 민감한 정보를 포함할 수 있다. 공개 저장소로 전환하거나 민감한 로그를 공유하지 않는다. 비공개 Git 저장소도 데이터 암호화를 대체하지 않는다.
- SQLite의 `-wal`, `-shm` 임시 파일을 Git으로 커밋하지 않는다. 서버가 종료된 상태에서 일관된 SQLite 백업본을 관리한다.
- **기존 `data/`의 DB나 Memory 저장소에 이미 존재하는 DB를 자동 삭제/덮어쓰기 하지 않는다.** 모든 충돌은 직접 검토한다.

## 1. Memory 저장소 복제 또는 기존 체크아웃 이름 변경

MAI 저장소 루트의 Git Bash에서 다음을 실행한다.

```bash
git clone https://github.com/FoggyCaligo/MAI_MEMORY.git MAI_MEMORY
```

이미 이전 이름인 `MAI_MyAI_sllm_Memory/`로 클론했다면 다시 클론하지 않고 Git Bash에서 `mv MAI_MyAI_sllm_Memory MAI_MEMORY`로 디렉터리 이름을 변경할 수 있다. 다만 `MAI_MEMORY/`가 이미 있다면 덮어쓰지 않는다. 저장소 자체 이름도 GitHub에서 `MAI_MEMORY`로 변경되었으므로 기존 체크아웃에서는 `git -C MAI_MEMORY remote set-url origin https://github.com/FoggyCaligo/MAI_MEMORY.git`로 origin 주소를 갱신한다. 비공개 저장소이므로 GitHub 접근 권한과 인증이 필요하다.

## 2. 기존 data 폴더 이전 (필요한 경우에만)

이미 `MAI_MEMORY/`에 최신 데이터베이스가 있다면 이 단계는 **생략**한다. 이전 도구는 `data/`의 내용을 새 저장소에 추가해야 할 때만 사용한다.

```bash
./portable/portable-runtimes/python/python.exe scripts/migrate_memory_data.py
```

계획 출력에 충돌이 없다면 **MAI가 중지된 상태**에서 실행한다.

```bash
./portable/portable-runtimes/python/python.exe scripts/migrate_memory_data.py --apply
```

- SQLite 파일은 `sqlite3.Connection.backup`으로 복사한다.
- 다른 데이터 파일도 하위 경로를 유지해 복사한다. 과거 `voice_profiles/` 백업은 이전 도구에서 복사될 수 있지만 MAI는 더 이상 사용하지 않는다.
- 기존 `data/` 폴더는 그대로 남긴다.
- 목적지에 같은 이름이 이미 존재하면 **실행 전에 실패**한다. Memory 저장소에 `memory.sqlite3`가 이미 있다면 기존 두 DB 중 어떤 데이터를 유지·병합할지 직접 결정해야 한다. 덮어쓰기 옵션은 제공하지 않는다.

## 3. 로컬 .env 경로 갱신

기존 MAI의 `.env`가 존재하면 자동으로 수정되지 않는다. 아래 기존 경로를 Memory 저장소 기준으로 직접 변경한다.

```dotenv
MEMORY_DB_PATH=./MAI_MEMORY/memory.sqlite3
CHAT_DB_PATH=./MAI_MEMORY/chat.sqlite3
LOG_DB_PATH=./MAI_MEMORY/log.sqlite3
SENTENCE_BREAKER_DB_PATH=./MAI_MEMORY/sentence_breaker.sqlite3
```

Windows/Linux Portable 실행기는 기본 DB 경로에 Memory 저장소를 사용한다.
설정값 `MEMORY_DB_PATH` 등의 명시적인 환경변수가 지정되어 있다면 해당 값이 우선한다.
벤치마크 서버는 `benchmark/GAIA Benchmark/result/_runtime/`의 네 SQLite DB 경로를 별도로 설정한다. 단, 업로드 경로·파일/터미널 도구의 OS 접근 범위까지 자동으로 분리하는 것은 아니다. [GAIA 실행 안내](../benchmark/GAIA%20Benchmark/docs/README.md)를 참고한다.

## 4. 두 저장소 각각의 Git 관리

MAI 본체의 `git status`에 `MAI_MEMORY/`의 내용이 나타나서는 안 된다.
메모리 데이터 변경 내역은 아래처럼 별도 저장소 안에서 확인한다.

```bash
cd MAI_MEMORY
git status
```

현재 사용하는 네 개의 DB가 `MAI_MEMORY/`에 존재하고, `.env` 및 MAI 서버의 경로 설정이 새 폴더를 가리키는 것이 확인된 뒤에는 기존 `data/` 폴더를 정리할 수 있다. **삭제는 사용자가 직접 수행한다.** 기존 경로가 남아 있는 커스텀 스크립트 또는 다른 프로세스가 없는지 먼저 확인한다.
**Git에서 DB를 커밋하기 전에 MAI 프로세스 및 SQLite 쓰기 작업을 종료**하고,
백업·개인 정보 노출 위험 및 용량을 확인해야 한다.


## 이전 파일과 Git 추적 이력 주의

현재 `dev` Git 트리에는 과거 `data/`에서 추적한 SQLite 바이너리 파일들이 남아 있다. **새 기본 경로를 `MAI_MEMORY/`로 변경한 것만으로 Git에 이미 추적된 `data/*.sqlite3`가 사라지지는 않는다.** 이 파일의 삭제·추적 해제·과거 Git 기록 정리는 별도 데이터 보존 및 이력 정책 검토 후 수행해야 하며, 이 문서는 해당 작업을 자동으로 수행하지 않는다. `MAI_MEMORY/`의 별도 Git 저장소 및 `.gitignore` 적용과 본체에 남은 과거 추적 파일은 구분한다.
