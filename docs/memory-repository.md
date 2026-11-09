# 독립 Memory Git 저장소 연결 및 데이터 이전

MAI는 개인 데이터베이스를 본체 코드 저장소와 분리한다. 기본 데이터 저장소 경로는
프로젝트 루트의 `MAI_MEMORY/`다. 이 폴더는 별도 Git 저장소이고
MAI 본체의 `.gitignore`로 추적하지 않는다.

## 안전 수칙

- **MAI와 원격 워커 등 관련 프로그램을 종료한 상태**에서 기존 데이터 이전 및 Git 커밋을 수행한다.
- Memory 저장소는 개인 대화·장기기억·로그·음성 특징 등 민감한 정보를 포함할 수 있다. 공개 저장소로 전환하거나 민감한 로그를 공유하지 않는다. 비공개 Git 저장소도 데이터 암호화를 대체하지 않는다.
- SQLite의 `-wal`, `-shm` 임시 파일을 Git으로 커밋하지 않는다. 서버가 종료된 상태에서 일관된 SQLite 백업본을 관리한다.
- **기존 `data/`의 DB나 Memory 저장소에 이미 존재하는 DB를 자동 삭제/덮어쓰기 하지 않는다.** 모든 충돌은 직접 검토한다.

## 1. Memory 저장소 복제

MAI 저장소 루트의 Git Bash에서 다음을 실행한다.

```bash
git clone https://github.com/FoggyCaligo/MAI_MEMORY.git MAI_MEMORY
```

이미 클론되어 있다면 다시 실행하지 않는다. 비공개 저장소이므로 GitHub 접근 권한과 인증이 필요하다.

## 2. 기존 data 폴더 이전 계획 확인

```bash
./portable/portable-runtimes/python/python.exe scripts/migrate_memory_data.py
```

계획 출력에 충돌이 없다면 **MAI가 중지된 상태**에서 실행한다.

```bash
./portable/portable-runtimes/python/python.exe scripts/migrate_memory_data.py --apply
```

- SQLite 파일은 `sqlite3.Connection.backup`으로 복사한다.
- 다른 데이터 파일 및 `voice_profiles/`도 하위 경로를 유지해 복사한다.
- 기존 `data/` 폴더는 그대로 남긴다.
- 목적지에 같은 이름이 이미 존재하면 **실행 전에 실패**한다. Memory 저장소에 `memory.sqlite3`가 이미 있다면 기존 두 DB 중 어떤 데이터를 유지·병합할지 직접 결정해야 한다. 덮어쓰기 옵션은 제공하지 않는다.

## 3. 로컬 .env 경로 갱신

기존 MAI의 `.env`가 존재하면 자동으로 수정되지 않는다. 아래 기존 경로를 Memory 저장소 기준으로 직접 변경한다.

```dotenv
MEMORY_DB_PATH=./MAI_MEMORY/memory.sqlite3
CHAT_DB_PATH=./MAI_MEMORY/chat.sqlite3
LOG_DB_PATH=./MAI_MEMORY/log.sqlite3
SENTENCE_BREAKER_DB_PATH=./MAI_MEMORY/sentence_breaker.sqlite3
VOICE_PROFILE_DIR=./MAI_MEMORY/voice_profiles
```

Windows/Linux Portable 실행기는 기본 DB 경로에 Memory 저장소를 사용한다.
설정값 `MEMORY_DB_PATH` 등의 명시적인 환경변수가 지정되어 있다면 해당 값이 우선한다.
벤치마크 서버는 여전히 독립된 `benchmark/GAIA Benchmark/result/_runtime/`을 사용하므로 시험용 결과는 개인 Memory 저장소에 섞이지 않는다.

## 4. 두 저장소 각각의 Git 관리

MAI 본체의 `git status`에 `MAI_MEMORY/`의 내용이 나타나서는 안 된다.
메모리 데이터 변경 내역은 아래처럼 별도 저장소 안에서 확인한다.

```bash
cd MAI_MEMORY
git status
```

기존 데이터 이전이 검증되기 전에는 원본 `data/`를 삭제하지 않는다.
**Git에서 DB를 커밋하기 전에 MAI 프로세스 및 SQLite 쓰기 작업을 종료**하고,
백업·개인 정보 노출 위험 및 용량을 확인해야 한다.
