# MAI 음성 입력: Web Speech와 로컬 STT

MAI의 음성 입력(STT, 음성을 텍스트로 변환)은 `settings.yaml`의 `VOICE_STT_PROVIDER`로 선택한다. 지원되는 값은 `web_speech`와 `local` 두 가지다. **음성 출력(TTS)과 STT는 별개의 기능**이며, 화면의 답변 음성 켜기/끄기 및 TTS 목소리 선택은 STT 제공자와 별도로 동작한다.

> 아래 내용은 현재 MAI의 구현(`mai/app/static/voice-input.js`, `mai/app/server.py`, `mai/app/voice.py`) 기준이다. 브라우저별 Web Speech 지원 여부와 실제 인식 서비스의 가용성은 버전·운영체제·환경에 따라 달라지며 MAI에서 보장하지 않는다.

## 방식 비교

| 항목 | `web_speech` | `local` |
|---|---|---|
| 텍스트 변환 | 브라우저의 `SpeechRecognition` 또는 `webkitSpeechRecognition` | MAI 서버의 `faster-whisper`(Whisper) |
| 음성 처리 위치 | 브라우저/브라우저 제공자의 구현에 따름. 원격 서비스로 음성이 전송될 수 있음 | MAI 서버에서 처리. MAI 음성 기능이 호스팅 STT 서비스로 녹음을 보내지 않음 |
| 입력에 필요한 브라우저 기능 | Web Speech Recognition API, 마이크 접근 | 마이크 녹음(`getUserMedia`) 및 Web Audio |
| 목소리 등록·화자 검증 | 사용하지 않음 | 사용하지 않음 |
| 서버 STT 모델 연산 | 필요 없음 | CPU `int8` Whisper, ONNX VAD 사용 |
| 외부 연결 | 브라우저 제공자의 동작에 따라 필요할 수 있음 | 최초 모델/의존성 확보에는 인터넷이 필요할 수 있으나, 모델 확보 후 STT 추론은 로컬 |
| 주요 제약 | API 지원 및 브라우저 음성인식 서비스의 가용성 | 서버 CPU·RAM, 모델 파일, 배경음·화자 혼합 시 인식 정확도 |

두 방식 모두 MAI의 **Owner 로그인**이 필요하다. 마이크 사용은 브라우저 보안 정책의 적용을 받으므로 **HTTPS 또는 localhost와 마이크 권한**을 확인한다. Tailscale 등으로 접속할 때도 실제 페이지의 보안 컨텍스트가 적합해야 한다.

## 제공자 선택과 적용

저장소 루트의 `settings.yaml`에서 다음 중 한 값을 설정한다.

```yaml
# 브라우저 음성인식
VOICE_STT_PROVIDER: web_speech
```

또는

```yaml
# MAI 서버의 로컬 Whisper 음성인식
VOICE_STT_PROVIDER: local
```

**현재 활성 값은 사용하는 브랜치·배포의 `settings.yaml`을 확인한다.** 이 문서의 코드 블록은 선택 예시이며 기본값이나 설치 상태를 의미하지 않는다. 설정을 변경한 뒤 **MAI 웹 서버를 재시작**하고 브라우저 페이지를 새로고침한다. 이 설정은 현재 사용자별 화면 토글이 아니라 **서버 전체의 STT 제공자 선택**이다. 서버 실행 시 환경 변수로 같은 키를 별도로 주입한다면 실제 프로세스의 적용 설정도 함께 확인한다.

같이 사용하는 설정:

| 키 | 용도 | 현재 설정 예시 |
|---|---|---|
| `VOICE_SILENCE_SECONDS` | 마지막 발화 뒤 입력을 마치는 대기 시간(1~120초) | `5` |
| `VOICE_WHISPER_MODEL` | 로컬 STT에 사용할 Whisper 모델 | `small` |

로컬 STT에서 화자 검증은 수행하지 않지만, **MAI Owner 로그인 인증은 계속 필요**하다.

## Web Speech 사용 방법

1. `VOICE_STT_PROVIDER: web_speech`를 적용한 MAI에 Owner로 로그인한다.
2. 마이크 접근을 허용하고 채팅창의 마이크 버튼을 누른다.
3. 한국어로 발화한다. 브라우저 음성인식은 `ko-KR`, 연속 인식 및 중간 결과 표시를 요청한다.
4. 인식된 최종 발화 뒤 지정된 무음 시간이 지나면 MAI가 메시지 자동 전송을 시도한다. 답변 후 음성 모드가 켜져 있으면 다시 듣는다.
5. 마이크 버튼을 다시 누르면 음성 모드를 끈다.

브라우저가 해당 API를 제공하지 않거나 인식 서비스가 동작하지 않으면 오류가 표시될 수 있다. `SpeechRecognition` 객체의 존재만으로 실제 변환 성공까지 보장되지는 않는다. MAI 서버는 Web Speech 방식의 녹음 내용을 Whisper에 보내지 않는다.

## 로컬 STT 사용 방법

1. `VOICE_STT_PROVIDER: local`로 설정하고 MAI 서버를 재시작한다.
2. Owner 계정으로 로그인한 뒤 마이크 버튼을 누른다. **목소리 등록은 필요하지 않다.**
3. 브라우저 WAV 녹음은 기존 Silero VAD에서 발화 종료 후 설정된 5초에 자동 종료된다.
4. 서버에서 VAD로 음성 구간을 추출한 뒤 `faster-whisper` CPU `int8`로 텍스트 변환하고, 기존 자동 전송 흐름을 이용한다. 기존 프로필 파일은 자동 삭제하지 않으며, 현재 STT에서는 읽거나 갱신하지 않는다.

로컬 음성 입력은 `/voice/activity`(발화 종료 감지) 및 `/voice/transcribe`(Whisper 변환)를 사용한다. 화자 등록용 `/voice/enroll` API는 제거되었다.

Whisper는 서버 CPU와 RAM을 사용하며, 이미 감지한 발화를 Whisper VAD로 중복 필터링하지 않는다. Python 의존성 중 일반 인식에 필요한 구성은 `faster-whisper`, `vadonnx`, `numpy`, `scipy` 등이다.

**주의:** 다른 사람의 목소리도 인식될 수 있으며, 음성 명령은 Owner가 로그인한 브라우저에서만 전송할 수 있다. 화자 확인을 제거했다고 해서 HTTP 인증이 해제된 것은 아니다.

## 문제 해결

| 증상 | 점검 사항 |
|---|---|
| 마이크 버튼이 비활성화됨 | HTTPS/localhost 보안 컨텍스트, `getUserMedia` 지원, 브라우저 마이크 권한 |
| Web Speech 미지원 메시지 | 브라우저에 `SpeechRecognition`/`webkitSpeechRecognition`이 있는지 확인; 필요하면 로컬 STT로 전환 |
| Web Speech 인식 오류 | 마이크 권한, 네트워크 및 해당 브라우저 인식 서비스 상태를 확인. 오류 종류는 화면에 표시됨 |
| `403 Forbidden` | Owner 로그인/세션 권한을 확인. 일반 로컬 STT에서는 `speaker_mismatch`가 발생하지 않음 |
| `insufficient_speech` (422) | 마이크 입력 및 발화 길이, VAD가 감지할 수 있는 실제 음성 구간을 확인 |
| 모델/패키지 로딩 오류 | 서버 로그의 구체적인 예외를 확인하고 해당 Python 의존성·모델 파일 다운로드 상태를 해결 |
| 로컬 인식이 느리거나 서버가 버거움 | CPU·RAM 사용량, 최초 모델 로드 시간 및 메인 LLM 동작 중 부하를 확인. 처리 속도는 장치에 따라 다름 |
| 설정을 바꿨는데 이전 방식으로 작동함 | 실제 서버 프로세스의 `VOICE_STT_PROVIDER` 설정, 재시작 및 페이지 새로고침 여부를 확인 |

### 현재 적용 상태 확인

Owner로 로그인한 MAI 화면에서 마이크를 눌렀을 때의 안내 메시지로 흐름을 구분할 수 있다. 더 직접적으로는 인증된 브라우저에서 호출되는 **`GET /voice/config` 응답의 `stt_provider` 필드**를 확인한다. `local`에서도 목소리 등록 여부와 관계없이 인식하며, 화자 검증 관련 설정값은 더 이상 반환하지 않는다. 해당 API는 Owner 인증이 필요하며, 미인증 호출의 실패를 서버 장애로 오인하지 않는다.

### 원래 방식으로 복원하기

`settings.yaml`의 `VOICE_STT_PROVIDER`를 이전 값(`web_speech` 또는 `local`)으로 되돌리고 MAI 서버를 재시작한다. 제공자 전환 및 화자 검증 코드 제거는 기존 목소리 프로필 파일을 삭제하지 않는다.

## 구현 위치

- `settings.yaml`: STT 제공자 및 인식 관련 설정
- `mai/app/settings.py`: 제공자 값 유효성 검사 및 설정 로드
- `mai/app/static/voice-input.js`: 마이크, Web Speech, 녹음, 자동 전송, TTS 연결
- `mai/app/server.py`: `/voice/config`, `/voice/activity`, `/voice/transcribe`
- `mai/app/voice.py`: VAD와 CPU Whisper 변환

이 문서는 **기능의 구현 계약 및 사용자 조작 방법**을 설명한다. 모든 브라우저/기기에서의 음성 입력 성공, 음성인식 정확도 및 실제 서버별 CPU·메모리 성능을 보장하거나 실기기 테스트 완료를 뜻하지 않는다.

## 화자 검증 제거 후 정리

화자 식별만을 위한 `speakeronnx` 및 WeSpeaker ResNet34 모델, `VOICE_MATCH_THRESHOLD`, `VOICE_PROFILE_DIR`, 목소리 등록 UI/API/코드를 제거했다. `vadonnx`·Silero VAD는 발화 감지와 5초 종료 판단에 필요하고, `faster-whisper`, `numpy`, `scipy`는 로컬 음성인식에 계속 필요하다.

기존 `MAI_MEMORY/voice_profiles/` 사용자 파일, 이미 설치된 Python 패키지와 공유 모델 캐시는 자동 삭제하지 않는다. 환경을 확인한 후 별도 패키지 정리가 필요하면 `python -m pip uninstall speakeronnx`를 사용할 수 있지만, 다른 프로젝트의 사용 여부도 먼저 확인해야 한다.

로컬 Whisper는 완성된 녹음을 서버로 전송한 뒤 변환하므로 Web Speech처럼 실시간 부분 전사를 표시하지 않는다. 다만 **메시지 입력창 위의 초기 듣기 안내 문구는 두 모드에서 동일**하게 표시한다.
