# Google Drive / Sheets 연동 (MAI Owner 전용)

이 기능은 MAI가 Google Drive 파일을 검색하고 Google Sheets 값을 직접 읽고 수정할 수 있게 한다. ChatGPT의 Google 커넥터와는 별개로 **본인의 Google Cloud OAuth 앱**이 필요하다.

## 1. Google Cloud 설정 (최초 1회)

1. https://console.cloud.google.com/ 에서 개인 프로젝트 생성.
2. **API 및 서비스 → 라이브러리**에서 Google Drive API, Google Sheets API 활성화.
3. **Google Auth Platform → Branding / Audience**에서 OAuth 동의 화면 구성. 개인 테스트 상태면 **Test users**에 사용할 Google 계정 추가.
4. **Clients → Create client → Desktop app**을 생성하고 OAuth 클라이언트 JSON을 다운로드.
5. 다운로드한 JSON을 **Git 저장소 밖의 비공개 폴더**에 보관. Google 계정의 비밀번호나 JSON 내용을 .env, PR, 채팅에 올리지 않는다.

OAuth scope:
- `https://www.googleapis.com/auth/drive.metadata.readonly`: 파일 검색/메타데이터
- `https://www.googleapis.com/auth/spreadsheets`: Google Sheets 읽기 및 수정

이 조합은 Google Sheets 전체에 읽기/쓰기 권한을 부여하므로 신뢰할 수 있는 **Owner**에게만 활성화된다. Drive 파일 삭제 권한은 포함하지 않는다.

## 2. MAI Windows PC에서 연결

MAI 프로젝트 폴더에서 가상환경을 활성화하고 최신 의존성을 설치:

```powershell
python -m pip install -e ".[dev]"
```

원하는 비공개 폴더를 만든다. 예: `$HOME\Documents\MAI-secrets`.

```powershell
New-Item -ItemType Directory -Force "$HOME\Documents\MAI-secrets"
```

다운로드한 OAuth Desktop client JSON을 그 폴더로 옮긴 후 파일명을 `google-client.json`으로 정한다. 다음 명령으로 브라우저 로그인 및 Google 계정 권한 동의를 진행한다.

```powershell
python scripts/google_oauth_login.py --client-secret "$HOME\Documents\MAI-secrets\google-client.json" --token "$HOME\Documents\MAI-secrets\google-token.json"
```

브라우저에서 동의하면 토큰이 해당 경로에 저장된다. 이미 토큰이 존재하면 덮어쓰지 않고 오류가 발생한다. 재연동하려면 **기존 토큰을 직접 백업/삭제한 뒤** 명령을 실행한다.

프로젝트 `.env`에 아래 설정을 추가한다. 환경변수 안에 `$HOME`을 문자 그대로 입력하지 말고 **실제 절대경로**를 적는다.

```dotenv
GOOGLE_OAUTH_TOKEN_PATH=C:/Users/본인계정/Documents/MAI-secrets/google-token.json
```

MAI를 완전히 종료한 후 `python run_server.py`로 재시작한다.

**주의:** Google OAuth 앱이 External / Testing 상태인 경우 승인 refresh token이 짧은 기간(일반적으로 7일) 내 만료될 수 있다. `invalid_grant`가 발생하면 OAuth 설정 상태를 점검하고 새로 인증해야 한다. 인증 실패를 MAI가 정상 조회 결과로 둔갑시키지 않는다.

## 3. 사용 예시

- “Google Drive에서 투자 기록 구글시트를 찾아줘.”
- “찾은 시트의 탭과 범위를 확인하고 A1:H20을 읽어줘.”
- “매도 내역의 G14 셀에 날짜를 기록해줘.”
- “기록 시트 마지막 행에 새 거래 내역을 추가해줘.”

동명이인이 아닌 **파일 고유 ID**를 사용해 조회한다. 단순히 제목만 보고 유일한 파일이라고 가정하지 않는다. 변경 작업 전에는 대상 파일/탭/범위와 현재 값을 확인하도록 요청하는 편이 안전하다.

## 4. 지원 도구

| 기능 | Native tool |
|---|---|
| Drive 검색 및 페이지 이동 | `google_drive_search` |
| 파일 정보 조회 | `google_drive_get_file` |
| 시트 탭/크기 조회 | `google_sheets_metadata` |
| A1 범위 읽기 | `google_sheets_read` |
| A1 범위 수정 | `google_sheets_write` |
| 표의 끝에 행 추가 | `google_sheets_append` |
| A1 범위 내용 삭제 | `google_sheets_clear` |

기존 MAI의 native tool execution과 감사 로그가 그대로 적용된다. Trial 계정에는 이 도구들이 등록되지 않는다. `GOOGLE_OAUTH_TOKEN_PATH` 미설정 시 Owner에게도 등록되지 않는다.

## 5. 문제 해결

- `GoogleNotConfiguredError`: 토큰 파일 또는 유효한 OAuth 인증이 없음.
- `HttpError 403`: 필요한 API가 비활성화됐거나 계정 권한/동의 범위가 부족함.
- `HttpError 404`: 대상 파일 ID가 없거나 연결한 Google 계정에서 볼 수 없음.
- `invalid_grant`: 토큰 취소·만료 등을 확인하고 다시 승인.
- 수정 실패: Google API의 예외가 명확히 전달된다. 성공 응답으로 숨기지 않는다.

## 현재 범위와 안전성

자동 삭제, Google Docs 편집, 시트 생성, 서식/차트 편집, 여러 계정별 토큰 관리는 구현하지 않았다. Owner 전체가 동일 로컬 토큰을 공유하는 **단일 개인 소유자용 연결**이므로, 복수 Owner 계정을 운영한다면 계정별 분리 설계가 필요하다. MAI의 감사 로그에는 읽은 데이터나 수정 결과가 기록될 수 있으므로 `log.sqlite3` 접근을 보호한다.
