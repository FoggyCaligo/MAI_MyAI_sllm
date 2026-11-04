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

## 발췌 근거 묶음과 원문 참조

후속 문맥의 JSON은 `view_type=selected_verbatim_evidence`인 근거 묶음이다. `excerpts`에는 모델이 선택한 원문만 담으며 별도 생성 요약은 만들지 않는다. 읽기 쉽도록 필드와 발췌를 들여쓰기로 구분한다.

- `selection.status`: 미선택 `none`, 일부 선택 `partial`, 전체 선택 `complete`를 구분한다. 빈 원문은 `complete`다.
- `selected_chars`와 `omitted_chars`: 선택한 문자 수와 묶음에 없는 문자 수다. 누락은 원문에 해당 정보가 없다는 뜻이 아니다.
- `original_read`: `tool_result_read` 이름과 바로 사용할 `arguments`를 제공하는 원문 참조다. 첫 누락 위치에서 읽으며 전체 선택 시 처음부터 재확인할 수 있다. 빈 원문에는 `null`이다.

모델은 필요하면 `tool_manual`로 `tool_result_read`를 활성화한 뒤 참조의 인자를 사용한다. 원문은 해당 실행 동안 보관한 관측 결과이며, 웹 검색이나 터미널 작업을 다시 실행하지 않는다. 발췌 좌표는 JSON으로 직렬화된 도구 원문 기준이며 파일 자체의 줄 번호와 다르다.

Verifier에는 실행 순서에 따른 `source_id`, 도구 이름, 같은 근거 묶음을 전달한다. `original_read_state.next_read_request`에는 verifier의 `source_id`에 맞춘 첫 미제공 구간의 읽기 인자가 들어간다. 이를 `read_requests`에 넣고 `review=null`로 요청할 수 있다. 추가 열람 후 참조도 남은 구간으로 갱신하며, 모두 제공되면 `null`이다. 이는 선택적인 재열람 안내이며 모든 원문을 읽도록 강제하지 않는다.

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
