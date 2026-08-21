# MAI sLLM Runtime Contract

이 문서는 `MAI_MyAI_sllm`의 최상위 실행 계약이다.
구현은 이 계약을 따라야 하며, helper/fallback/compatibility layer가 본 계약을 우회해서는 안 된다.

---

## 1. 제품 요구사항

1. **Tailscale 외부 접속**
   - Tailscale이 생성한 URL을 통해 외부에서 접속 가능해야 한다.
   - 서버는 로컬 바인딩을 유지하고 Tailscale Serve/Funnel이 외부 공개를 담당할 수 있다.

2. **UI**
   - 사용자에게 보이는 UI는 기존 MACHI/MK4 형식을 그대로 따른다.
   - 백엔드 구조는 새로 설계하되 시각적 형식과 주요 UI 동작은 유지한다.

3. **파일 입력 / 문서 / 이미지**
   - UI를 통한 파일 업로드가 가능해야 한다.
   - 문서 읽기 기능을 제공한다.
   - 이미지 읽기 기능을 제공한다.
   - 이미지 분석용 sLLM 모델은 일반 대화 모델과 분리하여 `.env`에서 별도로 설정 가능해야 한다.

4. **파일 탐색 및 CRUD**
   반드시 다음 모델용 도구를 제공한다.
   - `file_tree`
   - `file_read`
   - `file_create`
   - `file_update`
   - `file_delete`
   - `file_download_link`

   추가로 다음 두 도구는 역할이 다르므로 유지한다.
   - `file_search`: 파일명 / glob / 경로 기반 검색
   - `file_text_search`: 파일 내부 텍스트 검색

   `file_tree`, `file_search`, `file_text_search`는 서로 다른 역할을 가지며 하나로 합치지 않는다.

   소유자(owner) 계정의 파일 조회 및 CRUD에는 애플리케이션 레벨의 workspace 범위 제한을 두지 않는다.
   - 절대경로 허용
   - 상위 디렉터리 접근 허용
   - 실제 OS/filesystem 권한이 최종 권한 경계다.

5. **코드 관련 도구**
   - `code_search`는 유지한다.
   - `code_index`는 **모델 노출뿐 아니라 내부 구현에서도 사용하지 않는다.**
   - 별도 사전 코드 인덱스 구축 계층을 만들지 않는다.
   - `code_search`는 필요 시 직접 파일 구조와 소스 파일을 탐색하여 결과를 만든다.

6. **터미널**
   - `terminal_command`에는 소유자 기준 애플리케이션 레벨 권한 제한을 두지 않는다.
   - 실제 OS / shell / filesystem / registry / process 권한이 최종 권한 경계다.
   - 실제 실행 실패는 그대로 실패로 노출한다.

7. **기억 recall**
   - 모든 정상 턴은 일반 작업 전에 반드시 기억 recall을 먼저 수행한다.
   - 한 번의 recall은 **1 depth**만 확장할 수 있다.
   - 더 먼 관계를 확인하려면 모델이 추가 `recall_memory` 호출을 해야 한다.

8. **그래프 업데이트 시점과 범위**
   - 그래프 생성/수정은 사용자에게 보여줄 답변 draft가 완성된 이후에만 수행한다.
   - 그래프 생성/수정에 사용할 수 있는 의미적 근거는 다음으로 제한한다.
     - 현재 턴의 user 입력
     - 현재 턴의 고정된 sLLM 답변
     - 현재 턴에서 명시적으로 recall된 graph node/edge
   - 과거 문맥을 직접 참조하려면 먼저 recall되어야 한다.

9. **agent loop**
   - 한 model round는 정확히 한 action이다.
   - 한 응답에서 여러 tool call 또는 tool + answer를 함께 반환하지 않는다.
   - agent loop에는 임의의 global round cap을 두지 않는다.
   - 모델이 terminal action을 반환하거나 실제 오류/취소/종료가 발생할 때까지 계속될 수 있다.

10. **나머지 구조적 원칙**
   - 아래 계약을 따른다.

---

## 2. 모델과 Framework의 책임 분리

### 모델이 결정하는 것

모델은 의미를 결정한다.

- 무엇을 추가로 recall할지
- 어떤 tool을 사용할지
- tool 결과를 어떻게 해석할지
- 최종 답변 내용
- graph node의 의미와 이름
- graph edge/relation의 의미와 이름
- 어떤 기억을 새로 만들지
- 어떤 기억을 수정할지
- 현재 턴의 표현을 어떤 개념으로 정규화/일반화할지

Framework는 위 의미 결정을 문자열 규칙으로 대신하지 않는다.

### Framework가 강제하는 것

Framework는 실행 계약을 강제한다.

- 현재 phase
- phase별 허용 action/tool
- JSON schema
- account role / authentication
- graph ownership
- recall scope
- mutation scope
- DB transaction
- tool 실제 실행
- 성공/실패 여부
- memory phase에서 fixed answer 불변성

요약하면:

> **의미 판단은 모델, 실행 계약은 Framework.**

---

## 3. 문자열 하드코딩 / 의미 휴리스틱 금지

다음 방식으로 의미를 판단하지 않는다.

```text
if text contains ...
if filename contains ... then route ...
if answer contains correction phrase ...
```

금지 대상:
- tool route 결정
- correction 의도 판별
- 사람/AI/정체성 판단
- 관계 의미 판단
- memory 의미 판단
- malformed model output의 의미적 자동교정

Schema 위반은 contract failure로 드러나야 한다.

---

## 4. Sentence_Breaker 제거

`MAI_MyAI_sllm` runtime에서 다음을 전부 제거한다.

- Sentence_Breaker
- Sentence_Breaker DB 의존성
- `writable_terms`
- `term_id`
- segmentation 단계
- segmentation fallback

sLLM이 직접 semantic node 이름과 relation을 생성한다.

Sentence_Breaker 프로젝트와 기존 DB는 별도 MAI 연구 자산으로 유지할 수 있으나, 이 runtime의 필수/보조 계층으로 사용하지 않는다.

---

## 5. Turn Lifecycle

정상 턴은 아래 순서를 따른다.

```text
User Input
   ↓
Mandatory Recall
   ↓
Work / Tool Loop
   ↓
Fixed Answer Draft
   ↓
Mandatory Memory Mutation
   ↓
Memory Done
   ↓
Release Fixed Answer
```

Fixed answer는 memory commit이 성공적으로 끝나기 전에 사용자에게 release하지 않는다.

---

## 6. One Model Round = One Action

각 model response는 정확히 하나의 action만 표현한다.

예:

```json
{"action":"tool","tool":"recall_memory","arguments":{}}
```

또는

```json
{"action":"answer","content":"..."}
```

또는 해당 phase에서 허용된 경우:

```json
{"action":"done"}
```

다음은 금지한다.

```text
tool A + tool B
tool + answer
multiple tool calls in one round
```

필요하면 다음처럼 반복한다.

```text
model -> tool
framework -> result
model -> tool
framework -> result
model -> answer
```

---

## 7. Recall Contract

### Mandatory first recall

일반 작업 전에 recall은 반드시 한 번 성공해야 한다.

### One depth per call

`recall_memory` 한 번은 focus node 기준 1 depth neighborhood만 반환한다.

한 호출에서 recursive multi-depth expansion을 하지 않는다.

더 먼 관계가 필요하면:

```text
recall_memory(node A)
→ node B 확인
→ recall_memory(node B)
→ node C 확인
```

처럼 모델이 추가 호출한다.

### Stable graph IDs

Recall 결과에는 실제 stable ID가 포함되어야 한다.

예:

```json
{
  "nodes": [
    {"node_id": 17, "name": "로봇공학"}
  ],
  "edges": [
    {
      "edge_id": 8,
      "subject_node_id": 1,
      "relation": "좋아한다",
      "object_node_id": 17
    }
  ]
}
```

기존 memory 수정은 현재 턴에 recall되었거나 현재 턴에서 생성된 실제 ID만 사용할 수 있다.

---

## 8. Work / Tool Loop Contract

Mandatory recall 이후 모델은 필요한 만큼 tool을 반복 호출할 수 있다.

Tool 선택은 structured model action으로만 이루어진다.
Framework가 user text를 보고 tool을 대신 route하지 않는다.

Tool execution failure는 실제 실패 결과로 model에게 전달한다.

Agent loop에는 임의의 최대 round 수를 두지 않는다.
단, 사용자의 취소, 서버 shutdown, 모델 요청 자체의 실패, tool/system의 실제 fatal failure는 턴을 종료할 수 있다.

---

## 9. Answer Contract

모델이 `answer` action을 반환한 순간 그 텍스트가 **fixed answer draft**가 된다.

그 이후 memory phase에서는:

- 답변 수정 금지
- 답변 재생성 금지
- helper가 답변 교체 금지
- fallback이 다른 성공 답변 생성 금지

Memory phase가 성공하면 fixed answer를 그대로 release한다.
Memory phase가 실패하면 성공처럼 가장하지 않는다.

---

## 10. Semantic Graph Contract

### Node

최소 개념 필드:

```text
node_id       Framework-generated stable ID
user_id       Framework-enforced owner
name          model-authored semantic name
created_at    Framework metadata
updated_at    Framework metadata
```

### Edge

최소 개념 필드:

```text
edge_id
user_id
subject_node_id
relation       model-authored semantic relation
object_node_id
support_count
created_at
updated_at
```

### Provenance

정확한 character span은 강제하지 않는다.

각 graph mutation은 최소한 다음 provenance를 가진다.

```text
turn_id
source role/context
```

Node name / relation은 원문 substring일 필요가 없다.
모델은 현재 턴 문맥을 기반으로 의미를 정규화/일반화할 수 있다.

예:

```text
user: "어릴 때부터 로봇 만드는 걸 좋아했어"
model semantic node: "로봇공학"
```

허용한다.

---

## 11. Memory Commit Contract

Memory mutation은 **모든 정상 턴에서 최소 1회 성공해야 한다.**

첫 memory round에서는 `done`을 schema에 노출하지 않는다.

허용 가능한 mutation:

- `write_memory`
- `revise_memory` (현재 턴에 수정 가능한 recalled/created 대상이 있을 때만)

최소 1회의 mutation이 성공한 이후에만 `done`을 노출한다.

```text
memory phase start
→ write/revise required
→ mutation success
→ write/revise/done available
→ done
```

모델이 가장 쉬운 선택으로 곧바로 `done`으로 빠지는 것을 구조적으로 차단한다.

---

## 12. write_memory Contract

`write_memory`는 semantic graph를 직접 만든다.

모델은:
- 기존 recalled node를 endpoint로 사용할 수 있다.
- 현재 턴 문맥에서 새 node를 만들 수 있다.
- relation을 직접 정의할 수 있다.

개념 예:

```json
{
  "action": "tool",
  "tool": "write_memory",
  "arguments": {
    "subject": {"kind": "user"},
    "relation": "좋아한다",
    "object": {"new_node": {"name": "로봇공학"}}
  }
}
```

Framework는 `"로봇공학"`이라는 의미가 올바른지 문자열 규칙으로 검증하지 않는다.
Framework는 scope / ownership / transaction만 검증한다.

---

## 13. revise_memory Contract

`revise_memory`는 **현재 턴에서 recall되었거나 생성된 graph scope**만 수정할 수 있다.

모델이 임의의 node_id / edge_id를 상상해서 수정할 수 없도록, 현재 scope의 실제 ID만 schema에 노출한다.

예:

```json
{
  "action": "tool",
  "tool": "revise_memory",
  "arguments": {
    "edge_id": 12,
    "subject": {"existing_node_id": 1},
    "relation": "좋아한다",
    "object": {"new_node": {"name": "차"}}
  }
}
```

Revision collision / invalid ID / ownership violation은 그대로 실패로 드러낸다.
자동 병합이나 문자열 기반 보정은 하지 않는다.

---

## 14. Tool Contract

모든 모델용 tool은 최소 다음 구조를 가진다.

```text
name
description
JSON input schema
execution handler
```

모델은 schema 안에서 tool/action을 선택한다.
Framework는 의미적 routing을 하지 않는다.

Tool manual 기능을 사용할 경우에도 tool contract 자체를 우회하거나 동적으로 다른 의미로 바꾸지 않는다.

---

## 15. File Tool Contract

### Required model-visible tools

```text
file_tree
file_search
file_text_search
file_read
file_create
file_update
file_delete
file_download_link
```

역할:

- `file_tree`: 디렉터리 구조 탐색
- `file_search`: 파일명/glob 검색
- `file_text_search`: 파일 내부 문자열 검색
- `file_read`: 텍스트 파일 읽기
- `file_create`: 파일 생성
- `file_update`: 파일 수정
- `file_delete`: 파일 삭제
- `file_download_link`: 사용자 다운로드 링크 생성

Owner 계정에서는 workspace confinement를 두지 않는다.
실제 OS filesystem 권한이 최종 경계다.

---

## 16. Code Search Contract

모델용 코드 도구는 `code_search`만 유지한다.

`code_index`는:

- 모델에 노출하지 않는다.
- 내부 implementation layer로도 만들지 않는다.
- persistent/in-memory 사전 인덱스를 별도로 구축하지 않는다.

`code_search`는 필요 시 직접 file tree / source files를 읽어 구조적 검색을 수행한다.

---

## 17. Document / Image Contract

### document_read

문서 입력 파일을 읽을 수 있어야 한다.
최소 PDF/DOCX 읽기 기능을 유지한다.

### image_analyze

이미지 파일을 분석할 수 있어야 한다.

이미지용 모델은 대화 모델과 분리한다.
예:

```env
MAI_OLLAMA_MODEL=qwen...
MAI_OLLAMA_IMAGE_MODEL=...
```

이미지 분석 tool의 모델 선택은 서버 설정이 담당하며 일반 chat model 선택과 분리한다.

---

## 18. Terminal Contract

`terminal_command`는 owner 기준 애플리케이션 레벨의 인위적 권한 제한을 두지 않는다.

Framework가 임의로:

```text
이 명령은 위험해 보이므로 실행 불가
workspace 밖이라 실행 불가
registry라 실행 불가
startup이라 실행 불가
```

같이 의미적으로 차단하지 않는다.

실제 OS / shell 권한과 실행 결과가 권한의 기준이다.

명령 실패, permission denied, path not found 등 실제 오류는 그대로 반환한다.

---

## 19. Authentication / Owner Contract

Owner와 non-owner의 capability policy는 구조적으로 분리할 수 있다.

본 계약에서 owner는 최소 다음 권한을 가진다.

- unrestricted file path access at application level
- unrestricted file CRUD at application level
- unrestricted terminal invocation at application level

실제 OS 권한은 별도이며 우회하지 않는다.

---

## 20. Tailscale Hosting Contract

서버는 Tailscale URL을 통해 외부 접속 가능해야 한다.

Windows launcher는 최소 다음을 수행한다.

```text
python 확인
tailscale 확인
local server bind
secure cookie 설정
Tailscale Serve/Funnel 설정
public URL 확인
server 실행
```

Tailscale 설정 실패를 숨기지 않는다.

---

## 21. Error Contract

실패는 실패로 남긴다.

```text
schema 위반          -> contract failure
존재하지 않는 ID     -> scope failure
ownership 위반       -> authorization/scope failure
DB collision         -> database failure
tool 실패            -> tool failure
Ollama 실패          -> model failure
Tailscale 실패       -> hosting failure
```

금지:

- 오류 문자열 비교 후 성공 처리
- 임시 fallback으로 본체 성공처럼 처리
- guessed path / guessed ID 자동 대입
- malformed tool call의 의미적 자동 교정
- helper가 본체 response contract를 대신 만족

---

## 22. Logging Contract

로그는 사람이 읽기 쉽게 최소화한다.

정상 chat은 가능한 한 한 줄 summary로 표현한다.

예:

```text
[MAI] chat | recall=1.2s | answer=2.3s | memory=0.8s | tools=3
```

세부 diagnostics는 필요 시 API/debug data로 유지할 수 있다.

오류는 숨기지 않고 명시적으로 표시한다.

---

## 23. Implementation Order

계약 확정 이후 구현은 다음 순서를 기본으로 한다.

```text
1. Sentence_Breaker 제거
2. semantic graph storage 재구성
3. one-depth recall
4. write_memory
5. revise_memory
6. memory completion contract
7. file discovery/read
8. file CRUD/download
9. document/image
10. terminal
11. code_search
12. web/current-information tools
```

각 단계는 독립 PR로 추가하고, 로컬 테스트와 실제 실행 검증 후 다음 단계로 넘어간다.
