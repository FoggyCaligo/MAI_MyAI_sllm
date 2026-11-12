# 도구 결과 전달과 페이지네이션

## 웹 검색·페이지 결과의 후속 문맥 발췌 (2026-11-08)

`web_search`와 `web_fetch` 성공 결과에 한하여 도구 원문을 실행 중 `ToolResultStore`에 별도 저장한다. **최초 모델 가시 출력은 기존처럼 원문(8,000자 초과 시 첫 페이지)**이고, 그 최초 사용 이후 이어지는 모델 요청부터 도구 이력의 해당 결과를 짧은 구조화 발췌로 대체한다. 이 방식은 모델 교체·LLM 요약 요청 없이 Python에서 기계적으로 발췌하므로 작업 중 Main 모델 언로드를 요구하지 않는다. 일부 프롬프트 재구성에 따른 KV 재평가는 발생할 수 있다.

- `web_search`: 실제 검색 결과의 **순위·제목·URL·원래 스니펫**과 제공자 페이지 정보 유지. 없는 사실·URL을 생성하지 않는다.
- `web_fetch`: 요청/최종 URL, 문서 제목, HTTP 상태, 원문 **앞 2,200자**의 실제 텍스트, 실제 링크 일부와 페이지네이션 힌트 유지. 페이지 내용 전체를 요약했다는 의미가 아니며, 중요한 내용이 뒤에 있으면 추가로 조회해야 한다.
- 공통: `source_excerpt_only=true`, `full_original_result_id`, `full_original_chars`, `read_full_with=tool_result_read`, `max_read_chars` 제공. `tool_manual`로 `tool_result_read`를 활성화한 뒤 필요한 정확한 범위를 읽을 수 있다.
- 발췌가 모델 입력 한도를 초과하는 경우, 출처 정보를 임의로 절단하지 않고 원문 식별자·제목·URL만 남기는 참조형으로 축소한다.
- **감사 DB의 `tool_log`는 `execution.audit_content`로 원본 그대로 저장**한다. 검증기의 최초 도구 출력과 `tool_result_read` 근거 처리는 기존 구조를 유지한다. 실패한 웹 도구와 다른 범주의 도구에도 적용하지 않는다.

이는 토큰 기준의 동적 context-window 관리나 사실 중요도 순위화가 아니며, 웹페이지 맨 앞의 발췌가 사용자 질문에 반드시 가장 관련 있다고 보장하지 않는다. 긴 작업의 모든 컨텍스트 초과를 방지하는 기능도 아니다.

## 메인모델에 전달하는 범위

- 결과가 `MAX_INLINE_TOOL_RESULT_CHARS` 이하면 본문을 그대로 전달하고 후속 문맥에도 유지한다.
- 한도를 넘는 결과는 요청별 `ToolResultStore`에 원문을 보관하고, 최초 페이지를 한 라운드 보여준 뒤 후속 문맥에는 원문 식별자와 읽기 참조를 남긴다.
- 필요한 추가 범위는 기존 `tool_result_read`로 읽는다. 크기 제한과 페이지네이션은 이전 동작을 유지한다.

배포 한도는 8,000자이며 페이지 메타데이터도 포함한다. 원문 읽기 상한 `max_read_chars`는 7,488자다. 환경변수 없이 `MAIRuntime`을 직접 생성하면 내부 기본 예산은 10,666자다. 단위는 토큰·바이트가 아닌 Python Unicode 문자다.

페이지 첫 줄은 JSON 메타데이터이며 뒤에 원문 범위를 붙인다.

| 필드 | 의미 |
|---|---|
| `result_id`, `total_chars` | 원문 식별자·전체 문자 수 |
| `offset`, `returned_chars` | 현재 제공 위치·문자 수 |
| `next_offset`, `complete` | 다음 위치·원문 끝 도달 여부 |
| `max_read_chars` | 한 번의 읽기 상한 |
| `pagination` | 페이지 크기·현재 페이지·전체 크기·다음 위치 |

`complete=false` 또는 `pagination.has_more=true`면 일부 범위다. `tool_result_read`에는 관측한 `result_id`, 다음 `offset`, 허용 범위의 `limit`를 전달한다. 이 도구도 먼저 `tool_manual`로 활성화해야 한다.

## 검증기와 감사 기록

사실·수치 검증기에는 성공한 각 도구 실행의 최초 모델 가시 출력(`execution.content`)과 해당 호출 `arguments`를 실행 순서대로 전달한다. `arguments`는 툴 실행 이력과 동일한 인덱스에서 가져오며, 툴별 그룹화·중복 제거는 하지 않는다. 작은 결과는 본문, 큰 결과는 최초 페이지이며, 이후 `tool_result_read`의 성공 결과도 포함한다. 원문 발췌를 선택하거나 검증기가 별도의 읽기 요청으로 원문을 다시 여는 경로는 없다.

감사 로그의 `tool_log`에는 모델 입력 예산과 별도로 도구 원문을 기록한다. 원문 저장소는 실행별이며 다음 사용자 요청으로 자동 이전하지 않는다. 감사 DB를 모델의 추가 근거 검색에 사용하지 않는다.

## 도구 자체의 제한

`file_list`, `file_search`, `code_search`의 자체 `max_items`·`max_results` 제한은 유지한다. `collection`의 `returned_count`, `total_count`, `has_more`, `complete`가 관측 범위를 나타낸다. 제한으로 중단했으면 `total_count=null`이며 별도 전체 탐색으로 개수를 계산하지 않는다.

도구 자체가 잘라낸 결과와 원문 저장소의 페이지네이션은 다르다. 저장된 결과 전체를 읽어도 원래 도구가 탐색하지 않은 항목까지 얻는 것은 아니다. 제한된 관측으로 전체 수·완전한 부재를 단정하지 않는다.

## 웹 검색의 정상적인 0건 결과

DDGS가 정상적으로 빈 결과 목록을 반환했을 때 `web_search`는 `results=[]`, `status=no_results`, `search_scope=requested_query`, `message=이 검색어로 확인된 결과가 없습니다.`를 반환한다. 이것은 **검색 성공 + 해당 검색어 기준 결과 0건**이며 도구 실행은 `ok=true`이다. `suggested_next_action={type: reformulate_query, guidance: ..., mandatory: false}`를 함께 전달하여 다른 검색어·동의어·언어·검색 범위를 시도할 수 있음을 안내하지만 **재검색을 강제하지는 않는다**. `status=no_results`를 전역적인 정보 부재로 해석해서는 안 된다. 검색 제공자의 네트워크 실패·타임아웃·요청 제한 또는 구분 불가능한 예외는 `WebSearchError`와 도구 실행 `ok=false`를 유지한다. 원문 결과뿐 아니라 이후 모델 문맥에 남는 축약 결과에도 이 상태와 안내를 보존한다.

## 웹 검색의 빈 후속 페이지

`web_search`는 검색 제공자의 페이지를 최대 3개까지 조회한다. 앞 페이지에 사용 가능한 결과가 있고 후속 페이지가 비어 있으면 조회를 종료하고 기존 결과를 성공으로 반환한다. `provider_pages`는 완료된 페이지 수이며, `stopped_on_empty_page`는 종료를 일으킨 빈 페이지 번호다. 3페이지를 모두 조회한 경우에는 후자 값이 `null`이다.

DDGS는 정상적인 0건뿐 아니라 백엔드 실패 시에도 일반 `DDGSException("No results found.")`를 발생시킬 수 있다. 이 예외에는 신뢰 가능한 구분 필드가 없으므로 **첫 페이지와 후속 페이지 모두 제공자 오류로 유지**한다. 정상이 확인된 빈 결과 목록 `[]`만 0건으로 분류한다. 예외 메시지 문자열 비교로 오류를 정상 결과로 바꾸지 않는다. 이 한계는 DDGS가 구조적인 빈 결과/실패 구분을 제공하기 전까지 남는다.


## GAIA 웹 문서 조회 실패 진단

2026-11-09 GAIA 감사 로그에서 `web_search`가 아닌 `web_fetch`가 같은 Wikipedia URL 등을 반복 호출하며 실패한 사례가 확인됐다. HTTP 연결·전송·상태 코드 오류는 서로 다른 원인이므로 `web_fetch`가 공통 오류로 감쌀 때에도 원래 예외의 `cause_type`과 `cause`를 최종 오류 메시지에 포함하고 서버 로그에 예외 추적을 기록한다. 이전 감사 DB에 이미 기록된 단순 `WebFetchError` 메시지에서 하위 원인을 사후 추정할 수는 없다. `max_chars` 변경과 URL 프래그먼트 변경은 동일한 전송 장애를 해결한다는 근거가 없다.

실패 유형 보존은 오류 원인 진단을 개선하는 변경이며 실제 네트워크 연결·HTTP 접근을 자동으로 복구하지 않는다. 반복 호출 제한이나 모델 도구 선택 정책은 별도 설계 검토가 필요하다.


## web_fetch HTTP 클라이언트 식별

`web_fetch`는 `User-Agent: MAI/0.1 (https://github.com/FoggyCaligo/MAI_MyAI_sllm) httpx`를 보낸다. 이 값은 **프로그램 이름·버전·공개 프로젝트 주소**만 포함하며 사용자 이름, 전화번호, 전자우편 주소 등 개인 연락처는 포함하지 않는다. 2026-11-09 사용자 환경에서 동일한 User-Agent를 가진 Portable Python `httpx` 요청이 Wikipedia의 HTTP 200 응답을 받은 반면, 기본 `httpx` 식별 요청은 HTTP 403이었다. 이것이 모든 외부 사이트의 접근을 보장하는 것은 아니다.

공개 프로젝트 URL은 저장소 소유자를 찾아볼 수 있는 연결점이다. User-Agent는 요청자를 인증하는 정보가 아니므로 **이 문자열만으로 저장소 소유자가 직접 요청했다고 판단할 수 없고**, 제3자가 해당 소스를 사용하거나 User-Agent를 복제할 수 있다. 외부 서버는 자신의 접속 로그에 IP 주소 등 별도 정보를 기록할 수 있다. 배포용 코드에 개인 식별·연락 정보를 넣지 않는다.


## DDGS 시간초과 시 Google 재시도

`web_search`는 DDGS 기본 백엔드로 각 결과 페이지를 조회한다. **시간초과 예외 타입**(`ddgs.exceptions.TimeoutException`, Python `TimeoutError`, `httpx.TimeoutException`) 또는 예외 원인 체인에 해당 타입이 있는 경우에만 동일 페이지를 `backend="google"`로 한 번 재시도한다. 이는 검색 공급자를 DDGS에서 교체하는 것이 아니다.

재시도가 성공해도 초기 시간초과를 경고 로그로 남기고, 반환값의 `fallback_backend`와 `fallback_pages`에 실제 사용 사실을 기록한다. 페이지 결과 병합·URL 중복 제거·페이지별 종료 규칙은 유지한다. Google에서도 실패하면 실패를 숨기지 않고 기존 `WebSearchError`를 발생시킨다.

**예외 메시지 문자열을 보고 오류 의미를 추측하지 않는다.** DDGS가 Windows 네트워크 타임아웃 `10060`을 원인 체인 없는 일반 `DDGSException`으로 변환하는 경우에는 자동 Google 재시도가 실행되지 않는다. 이 경우 DDGS의 구조화된 원인 전달을 별도로 개선해야 한다.
