# 도구 결과 전달과 원문 재열람

도구 원문은 요청별 `ToolResultStore`에 수정 없이 보관하고 SHA-256과 `result_id`를 부여한다. 모델에는 최초 페이지를 한 차례 보여준 뒤, 모델이 선택한 원문 발췌와 출처 참조만 남긴다. 별도 요약 모델을 호출하거나 선택하지 않은 원문을 삭제하지 않는다.

## 최초 페이지와 제한

배포 `MAX_INLINE_TOOL_RESULT_CHARS`는 8,000자이며 메타데이터도 포함한다. 원문 읽기 상한 `max_read_chars`는 7,488자다. JSON 크기에 따라 최초 본문은 더 줄어들 수 있다. 환경변수 없이 `MAIRuntime`을 직접 생성하면 내부 기본 예산은 10,666자다. 단위는 토큰·바이트가 아닌 Python Unicode 문자다.

최초 페이지와 `tool_result_read` 결과는 첫 줄의 JSON 메타데이터와 뒤의 원문으로 구성한다.

| 필드 | 의미 |
|---|---|
| `result_id`, `total_chars` | 원문 식별자·전체 문자 수 |
| `offset`, `returned_chars` | 현재 제공 위치·문자 수 |
| `next_offset`, `complete` | 다음 위치·원문 끝 도달 여부 |
| `max_read_chars` | 한 번의 읽기 상한 |
| `pagination` | 페이지 크기·현재 페이지·전체 크기·다음 위치 |

`pagination.has_more=true` 또는 `complete=false`면 원문 일부다. 현재 페이지의 항목 수를 전체 결과 수로 해석하지 않는다. 다음 읽기에는 같은 식별자와 `next_offset`을 사용한다.

## 모델이 선택하는 근거 묶음

본 모델은 다음 구조로 원문 범위를 보존한다. 도구 활성화나 다른 호출과 같은 차례에 묶을 수 있다.

```json
{
  "tools": [],
  "retain_evidence": [{"result_id": "관측한 식별자", "ranges": [{"offset": 0, "length": 100}]}]
}
```

이 인자는 `tool_manual`에 전달한다. 실제 원문 범위여야 하며 겹침·범위 초과·예산 초과는 잘라내지 않고 실패한다. 선택 갱신은 해당 결과의 이전 선택을 대체하며 빈 `ranges`는 선택을 비운다. 선택 실패 시 도구 활성화도 부분 반영하지 않는다. 생각이나 최종 답변에 사실을 적는 것만으로 원문이 선택되지는 않는다.

후속 문맥과 사실 검증기의 기본 입력은 들여쓴 JSON 근거 묶음이다.

| 필드 | 의미 |
|---|---|
| `view_type=selected_verbatim_evidence` | 원문 발췌 묶음이며 생성 요약이 아님 |
| `result_id`, `source_sha256`, `total_chars` | 원문 식별·무결성·전체 크기 |
| `selection.status` | `none` 미선택, `partial` 일부, `complete` 전체 선택 |
| `selection.selected_chars`, `omitted_chars` | 선택한 문자 수·묶음에 없는 문자 수 |
| `excerpts` | 범위별 `offset`, `length`, 원문 `content` |
| `original_read` | 원문 읽기 도구 이름과 바로 사용할 인자 |

빈 원문은 전체 선택 상태이며 `original_read=null`이다. 선택이 없으면 발췌는 비지만 출처는 남는다. **묶음에 없다는 것은 원문에 없다는 뜻이 아니다.** 조건이나 반대 근거가 빠질 수 있다.

## 원문으로 돌아가는 참조

`original_read`는 다음과 같은 구조다.

```json
{
  "tool": "tool_result_read",
  "arguments": {"result_id": "관측한 식별자", "offset": 100, "limit": 500}
}
```

첫 누락 위치를 가리키며 전체 선택 시 처음부터 재확인할 수 있다. 모델은 필요하면 먼저 `tool_manual`로 읽기 도구를 활성화한 뒤 인자를 사용한다. 웹 검색·명령을 다시 실행하지 않고 당시 보관한 관측 결과를 읽는다. 읽은 페이지를 다시 별도 원문으로 중첩 저장하지 않는다.

좌표는 **직렬화된 도구 원문** 기준이다. 파일 자체의 줄 번호나 문서 본문만의 문자 위치와 다를 수 있다. 추가 페이지를 보존하려면 같은 원문 식별자로 필요한 범위를 선택한다.

검증기는 실행 순서에 따른 `source_id`, 도구 이름, 같은 발췌 묶음을 받는다. `original_read_state.next_read_request`는 검증기의 출처 ID에 맞춘 첫 미제공 구간의 읽기 인자다. 열람 후 갱신하며 모두 제공되면 `null`이다. 재열람은 선택 사항이고 판정·읽기 형식과 진행 제한은 [답변 검증](verifier.md)을 따른다.

## 도구 자체 제한과 감사 기록

`file_list`, `file_search`, `code_search`는 자체 `max_items`·`max_results`에 걸릴 수 있다. `collection`의 `returned_count`, `total_count`, `has_more`, `complete`가 범위를 나타낸다. 제한 안에서 전체 탐색이 끝났으면 전체 수는 반환 수와 같다. 제한으로 중단했으면 `total_count=null`이며 별도 전체 탐색으로 개수를 계산하지 않는다.

도구 자체가 잘라낸 원문과, 원문 저장소가 페이지로 나눠 제공한 상황은 다르다. 저장된 결과 전체를 읽어도 원래 도구가 탐색하지 않은 항목까지 얻는 것은 아니다. 제한된 관측으로 전체 수·완전한 부재를 단정하지 않는다.

감사 로그의 `tool_log`에는 모델 입력 예산과 무관하게 원문과 `result_id`를 기록한다. 실행별 원문 저장소는 다음 요청에 자동 이전되지 않으며 감사 DB를 모델의 추가 근거 검색에 사용하지 않는다.
