# Tool result pagination contract

MAI는 tool output 전체를 model history에 계속 누적하지 않는다. Production에서는 `ToolResultStore`가 크기에 관계없이 관측 결과 원본과 SHA-256을 run-local store에 보존하고, model에는 bounded page를 한 라운드 보여준다. 소비한 뒤에는 선택한 원문 발췌와 참조만 남긴다. 감사 로그의 `tool_log`에는 원본과 result_id를 보존한다.

배포 `settings.yaml`의 `MAX_INLINE_TOOL_RESULT_CHARS`는 8,000자이며, 페이지 메타데이터도 이 예산에 포함됨. `max_read_chars`는 7,488자(본문 한도에서 512자 제외)이고 실제 본문은 JSON 크기에 따라 더 줄어들 수 있음. 직접 `MAIRuntime`을 구성하고 환경변수가 없으면 내부 기본값은 10,666자임. 단위는 토큰이나 UTF-8 바이트가 아닌 Python Unicode 문자임.

## Large tool-result page metadata

큰 결과의 model-visible page 첫 줄은 JSON metadata다. 핵심 필드는 다음과 같다.

```json
{
  "result_id": "...",
  "total_chars": 5000,
  "offset": 0,
  "returned_chars": 512,
  "complete": false,
  "pagination": {
    "page": 1,
    "page_size": 512,
    "returned_count": 512,
    "total_count": 5000,
    "total_pages": 10,
    "has_more": true,
    "next_page": 2,
    "next_offset": 512
  }
}
```

`pagination.has_more=true` 또는 `complete=false`이면 현재 보이는 내용은 full tool result가 아니다. 현재 page 안에서 보이는 item 수를 전체 collection 수로 해석하면 안 된다.

다음 범위는 `tool_result_read`에 같은 `result_id`와 `pagination.next_offset`을 사용해 읽는다.

Model은 기존 `tool_manual`의 `retain_evidence` 인자로 `{result_id, ranges: [{offset, length}]}`를 지정한다. 다음 history에는 source SHA-256, total_chars, max_read_chars, 원문 범위별 excerpts만 남긴다. 별도 요약 LLM은 호출하지 않는다. 선택하지 않은 내용은 입력에서 빠지지만 원본에서 삭제되지 않는다. 선택이 없으면 발췌는 비어 있고 원본 참조만 남는다.

offset/length는 0-based Python Unicode 문자 단위다. 범위를 벗어나거나 겹치는 선택, 예산을 초과하는 발췌는 잘라내지 않고 실패한다. `tool_result_read`는 같은 result_id의 원문 범위를 다시 보여주며, 이 읽기 결과를 또 다른 원본으로 중첩 저장하지 않는다.

Verifier도 같은 발췌를 기본 근거로 받고, 필요하면 구조화된 read_requests를 통해 성공한 원래 출처의 범위를 재열람한다. 자세한 요청/판정 계약은 [verifier.md](verifier.md)를 따른다.

## Collection-producing tools

`file_list`, `file_search`, `code_search`는 자체 `max_items` / `max_results` 제한에 걸릴 수 있다. 이 경우 결과에 `collection` metadata를 포함한다.

```json
{
  "returned_count": 200,
  "total_count": null,
  "has_more": true,
  "complete": false
}
```

전체 scan이 제한 안에서 끝난 경우에는 `complete=true`이고 `total_count=returned_count`다.

제한 때문에 중단된 경우 전체 개수를 추가 full scan으로 계산하지 않는다. 따라서 `total_count=null`을 유지하고, 관측된 결과만으로 전체 개수를 단정하지 않는다.

## Failure / grounding rule

Pagination metadata는 결과 범위를 나타내는 evidence다. 이를 문자열 heuristic으로 해석하거나 누락된 page를 성공/완료로 간주하는 fallback은 두지 않는다.

Final grounding verifier의 기존 scope-preservation 계약에 따라 partial collection evidence는 exhaustive collection claim을 지원하지 않는다.
