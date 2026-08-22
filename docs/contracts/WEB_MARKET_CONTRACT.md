# Mai Web / Market Tool Contract

이 문서는 `latest_search`, `web_research`, `market_snapshot` 세 모델-visible tool의 실행 계약을 정의한다.

## 공통 원칙

- 의미 판단은 모델이 한다.
- Framework는 schema와 실제 실행만 담당한다.
- 사용자 문장이나 query 문자열을 `if text contains ...` 식으로 분류하지 않는다.
- provider 실패를 다른 provider 성공으로 몰래 바꾸지 않는다.
- 실제 HTTP/provider 실패는 그대로 실패로 드러난다.

## latest_search

목적: 최신성 중심의 얕은 검색.

입력:

```json
{
  "query": "모델이 직접 작성한 검색어",
  "limit": 8
}
```

Framework는 query를 생성, 확장, 분류, 재작성하지 않고 그대로 recent/news provider에 전달한다.

반환값은 최소 title, URL, snippet, source와 provider가 제공하는 publication/freshness metadata를 포함할 수 있다.

## web_research

목적: 여러 검색 query를 사용해 일반 웹을 조사하고 상위 public page를 읽어 evidence package를 만드는 것.

입력:

```json
{
  "objective": "조사 목표",
  "queries": [
    "모델이 직접 만든 query 1",
    "모델이 직접 만든 query 2"
  ],
  "preferred_domains": [],
  "pages_to_read": 3
}
```

중요:
- `queries`는 모델이 직접 만든다.
- Framework는 objective에서 query를 자동 생성하지 않는다.
- Framework는 전달된 queries만 실행한다.
- `preferred_domains`는 모델이 명시한 결과 정렬 힌트다.
- page read는 public HTTP(S)만 허용하며 redirect 목적지도 매 단계 public address인지 재검증한다.
- page read 실패는 `page_errors`에 드러난다.

## market_snapshot

목적: 시장 asset 후보 lookup과 snapshot 조회.

Framework는 자연어 query나 symbol 문자열을 보고 asset 종류를 추론하지 않는다.

모델이 `provider_scope`를 명시한다.

허용 scope:

```text
kr_equity
global_equity
index
fx
```

### lookup

```json
{
  "operation": "lookup",
  "provider_scope": "kr_equity",
  "query": "삼성전자",
  "limit": 5
}
```

Framework는 해당 scope의 configured provider에 query를 그대로 전달하고 실제 후보를 반환한다.

후보는 provider가 사용할 실제 `provider_symbol`을 포함한다.

### snapshot

```json
{
  "operation": "snapshot",
  "provider_scope": "kr_equity",
  "provider_symbol": "005930.KS"
}
```

Framework는 `provider_symbol`을 다시 해석하거나 보정하지 않고 configured provider에 그대로 전달한다.

## Provider configuration

현재 기본 provider는 Yahoo다.

```env
MAI_MARKET_KR_EQUITY_PROVIDER=yahoo
MAI_MARKET_GLOBAL_EQUITY_PROVIDER=yahoo
MAI_MARKET_INDEX_PROVIDER=yahoo
MAI_MARKET_FX_PROVIDER=yahoo
```

scope별 provider 선택은 명시적인 실행 설정이며 query 의미 해석이 아니다.

현재 등록되지 않은 provider 이름을 설정하면 `market_snapshot`은 명확하게 실패한다. 자동 fallback은 없다.

## Internal helpers

search/page/market provider 구현은 내부 helper일 수 있지만 model-visible response contract를 대신하지 않는다.

`internet_search`, `web_page_read` 같은 low-level helper를 별도 model-visible tool로 노출할 필요는 없다.
