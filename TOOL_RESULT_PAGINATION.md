# Tool result pagination contract

MAI는 큰 tool output 전체를 model history에 그대로 누적하지 않는다. `ToolResultStore`가 full content를 run-local store에 보존하고 model에는 bounded page를 보여준다.

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

History에는 큰 첫 page 자체 대신 compact reference를 남긴다. 이 reference에도 initial page와 max read size 기준 총 page 수가 포함된다.

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
