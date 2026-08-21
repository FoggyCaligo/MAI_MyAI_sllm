# MK5 Runtime

## 1. 환경 설정

```powershell
Copy-Item .env.example .env
```

최소한 아래 값은 실제 환경에 맞게 바꾼다.

```env
MAI_OLLAMA_MODEL=qwen3.5:9b
MAI_OWNER_ID=<접속에 사용할 owner ID>
```

이미지 모델은 이후 `image_analyze` 구현에서 사용할 별도 설정이다.

```env
MAI_OLLAMA_IMAGE_MODEL=
```

## 2. 설치

```powershell
python -m pip install -r requirements.txt
```

## 3. 로컬 실행

```powershell
python run_server.py
```

기본 주소:

```text
http://127.0.0.1:8000
```

서버는 localhost에만 bind한다. 외부 공개를 위해 bind 주소를 넓히지 않는다.

## 4. Tailscale Funnel 공개

Tailscale이 설치되고 로그인된 Windows 환경에서:

```powershell
.\start_public_tailscale.ps1
```

또는:

```cmd
start_public_tailscale.cmd
```

스크립트는 로컬 MK5 서버를 `127.0.0.1:8000`에서 실행한 뒤 현재 Tailscale CLI의 Funnel reverse proxy를 설정한다.

```text
tailscale funnel --bg --yes http://127.0.0.1:8000
```

Funnel 설정 실패, Tailscale 미설치, Python 서버 조기 종료는 성공으로 숨기지 않고 오류로 종료한다.

## 5. 현재 UI/runtime 범위

- MK4 스타일의 MK5 채팅 UI
- `.env` 기반 대화 모델 표시 및 사용
- 허용 ID 로그인 / owner role 구분
- 새로고침 후 SQLite chat history 복원
- 파일 업로드 (`.mai_uploads/`)
- 현재 `AgentLifecycle` 호출
- work tool log 표시

아직 이 단계에서 업로드 파일 내용을 자동으로 읽지는 않는다. 실제 `file_*`, `document_read`, `image_analyze` 도구는 후속 PR에서 work-tool registry에 연결한다.
