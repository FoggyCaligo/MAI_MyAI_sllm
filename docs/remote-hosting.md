# MAI 원격 웹 호스팅 설정 (Windows / Linux)

이 문서는 **MAI 웹 UI를 외부 인터넷으로 공개하는 기능**만 다룬다. Agent, Memory, Verifier, 모델 서버, 다른 PC에 명령을 실행하는 `mai/remote/`와는 별개의 계층이다.

## 선택기와 기본 동작

프로젝트 루트의 `settings.yaml`에서 하나만 고른다.

```yaml
REMOTE_HOSTING_PROVIDER: tailscale   # tailscale | cloudflare | none
```

| 선택값 | MAI가 시작하는 기능 | 호스트 준비 |
|---|---|---|
| `tailscale` (기본값) | Tailscale Funnel 공개 | Tailscale 설치·로그인·Funnel 허용 |
| `cloudflare` | Cloudflare Named Tunnel 커넥터 | cloudflared 설치·토큰·공개 호스트명 라우팅 |
| `none` | 아무 공개 커넥터도 시작하지 않음 | 로컬 웹은 계속 사용 가능 |

소스 구조: `mai/hosting/manager.py`는 제공자를 선택하고 `mai/hosting/tailscale.py`, `mai/hosting/cloudflare.py`가 서비스별 실행을 담당한다. `mai/app/server.py`는 이 어댑터를 시작·종료한다. 향후 제공자를 추가하려면 `mai/hosting/`에 구현과 선택 분기를 추가하면 된다.

MAI 로컬 웹 주소는 기본 `http://127.0.0.1:8000`이다. 기본값이 `tailscale`이어도 Tailscale이 설치되지 않았거나 로그인·Funnel 권한이 없으면 외부 공개가 성립하지 않는다. 현재 코드는 시작 실패를 로그에 남기고 MAI 로컬 서버를 유지한다. **접속 가능 여부는 반드시 실제 외부 장치에서 검증**한다.

기존 `.env` 및 프로세스 환경변수에 `REMOTE_HOSTING_PROVIDER`가 있으면 `settings.yaml`보다 우선한다. 오래된 `TAILSCALE_FUNNEL` 항목은 더 이상 사용하지 않는다. 기존 개인 `.env`에서 해당 줄을 직접 삭제해도 된다.

## Tailscale Funnel (기본)

1. MAI를 실행할 Windows/Linux 호스트에 Tailscale을 설치하고 자신의 계정으로 로그인한다.
2. Tailscale 관리 정책에서 해당 장치의 Funnel 사용을 허용한다.
3. `settings.yaml`에서 `REMOTE_HOSTING_PROVIDER: tailscale`로 선택한다.
4. MAI를 실행하고 `tailscale funnel status`에서 공개 상태를 확인한다. 다른 네트워크의 브라우저에서 실제 로그인 테스트를 한다.

Tailscale Funnel은 공개 HTTPS 주소를 제공한다. 개인 Tailnet 사용자에게만 접속을 허용하는 Tailscale Serve와 달리, Funnel은 인터넷에 웹 로그인 화면을 공개한다. 강력한 오너 암호와 체험 계정 권한 설정을 먼저 확인한다.

**전환 시 주의:** MAI가 설정한 Tailscale Funnel은 기존과 같이 `--bg`로 실행되며 MAI 종료 후에도 **계속 남을 수 있다**. `cloudflare` 또는 `none`으로 변경해도 기존 Funnel을 자동 제거하지 않는다. `tailscale funnel status`로 확인하고, 해당 호스트에서 사용 중인 Funnel 설정에 맞는 `tailscale funnel ... off` 명령으로 필요 없는 규칙을 명시적으로 해제한다. 다른 애플리케이션의 규칙을 일괄 삭제하지 않는다.

## Cloudflare Named Tunnel

1. Windows/Linux 호스트에 공식 `cloudflared` 실행 파일을 설치하고 `PATH`에 등록한다. Portable USB에 MAI만 복사해도 cloudflared 설치가 자동 완료되지는 않는다.
2. Cloudflare Dashboard에서 **원격 관리형 Named Tunnel**을 만든다.
3. 해당 터널에 공개 호스트명(Cloudflare에서 관리되는 도메인·서브도메인)을 연결한다. 원본 서비스 주소는 `http://127.0.0.1:8000`으로 지정한다.
4. Dashboard에서 발급받은 **터널 커넥터 토큰**을 프로젝트 루트의 Git 추적 제외 파일 `.env`에 추가한다.

```dotenv
CLOUDFLARE_TUNNEL_TOKEN=여기에_발급된_실제_토큰
```

5. `settings.yaml`에 `REMOTE_HOSTING_PROVIDER: cloudflare`를 입력하고 MAI 서버를 재시작한다. Cloudflare Dashboard에서 커넥터의 Healthy 상태를 확인하고, 외부 HTTPS 주소로 접근해 로그인 테스트를 한다.

현재 구현은 `cloudflared tunnel run`을 MAI의 자식 프로세스로 실행한다. 토큰은 명령행 인자가 아닌 자식 프로세스의 `TUNNEL_TOKEN` 환경변수로 전달하며, 실행 로그에 토큰을 출력하지 않도록 표준 출력을 차단한다. MAI가 정상 종료되면 **자신이 시작한** cloudflared 프로세스를 종료한다. Dashboard의 터널 정의와 DNS 설정은 삭제하지 않는다.

이 구현은 **Cloudflare 계정·관리형 터널·공개 도메인 설정을 자동으로 생성하지 않으며**, 임시 `trycloudflare.com` 주소를 발급하는 모드도 아니다. Cloudflare 무료 제공 범위라도 자체 도메인 확보에는 별도 비용이 발생할 수 있다.

## 설정 전환·오류 확인

- `none`: 이번 MAI 프로세스는 새 공개 커넥터를 시작하지 않지만 **이전에 별도로 설치된 Tailscale Funnel이나 cloudflared 서비스까지 자동 종료시키지 않는다**.
- `tailscale`: `tailscale` 실행 파일과 로그인 및 Funnel 정책을 점검한다. `tailscale funnel status`로 확인한다.
- `cloudflare`: `cloudflared`가 PATH에 있는지, `CLOUDFLARE_TUNNEL_TOKEN`이 정확한지, Dashboard 공개 호스트명과 서비스 주소가 맞는지 확인한다.
- `/health`의 `remote_hosting_provider`는 이번 MAI 프로세스에서 제공자 실행을 시작했는지 보여주며, 실제 외부 HTTPS 접속 성공 여부를 보장하지 않는다. 예전 호환 필드 `tailscale_funnel`도 유지된다.
- 잘못된 `REMOTE_HOSTING_PROVIDER` 값은 명시적으로 오류가 발생한다. 모델 추론이나 장기기억 데이터는 제공자 전환으로 변경하지 않는다.
- 원격 PC 명령 실행 세션의 **10시간 실제 작업 유휴 만료**는 별도 기능이다. 호스팅 연결이 열려 있다고 원격 PC 실행 권한이 무기한 유지되는 것은 아니다. `dev`에서 이 규칙은 에이전트 폴링으로 갱신되지 않으며, 별도의 10분 초기 연결과 3분 연결 상태 확인도 유지한다.

## 보안 및 테스트

오너는 파일과 터미널 도구를 실행할 수 있으므로, 외부 공개 전에 `OWNER_USERS` 비밀번호를 변경하고 불필요한 `TRIAL_USERS`는 `[]`로 비활성화한다. 원격 PC에 대한 명령 실행 권한은 일반 웹 계정과 분리해 관리한다. 토큰과 개인 DB는 Git에 올리지 않는다.

```bash
python -m pytest -q tests/test_hosting_providers.py
python -m pytest -q
```

Windows/Linux 실기기에서 외부 접속을 확인해야 하며, 단순히 GitHub 코드가 병합되었다고 연결 검증이 완료된 것은 아니다.
