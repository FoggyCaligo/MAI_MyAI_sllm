# MAI sLLM Runtime Contract

이 문서는 `MAI_MyAI_sllm`의 최상위 실행 계약이다. 구현은 이 계약을 따라야 하며 helper, fallback, compatibility layer가 본 계약을 우회해서는 안 된다.

## 1. 제품 요구사항

1. **Tailscale 외부 접속**
   - Tailscale이 생성한 URL을 통해 외부에서 접속 가능해야 한다.
   - 서버는 로컬 바인딩을 유지하고 Tailscale Serve/Funnel이 외부 공개를 담당할 수 있다.
   - Tailscale 설정 실패는 숨기지 않는다.

2. **UI**
   - 사용자에게 보이는 UI는 기존 MACHI/MK4 형식을 따른다.
   - 백엔드 구조는 새로 설계하되 시각 형식과 주요 UI 동작은 유지한다.

3. **파일 입력 / 문서 / 이미지**
   - UI 파일 업로드를 지원한다.
   - `document_read`로 최소 PDF/DOCX를 읽을 수 있어야 한다.
   - `image_analyze`로 이미지를 읽을 수 있어야 한다.
   - 이미지 분석용 sLLM은 일반 대화 모델과 분리하여 `.env`에서 `MAI_OLLAMA_IMAGE_MODEL`로 설정한다.

4. **파일 탐색 및 CRUD**
   모델용 도구:
   - `file_tree`
   - `file_search`
   - `file_text_search`
   - `file_read`
   - `file_create`
   - `file_update`
   - `file_delete`
   - `file_download_link`

   역할은 각각 다르다.
   - `file_tree`: 디렉터리 구조 탐색
   - `file_search`: 파일명/glob/경로 검색
   - `file_text_search`: 파일 내부 텍스트 검색

   owner 계정의 파일 조회/CRUD에는 application-level workspace confinement를 두지 않는다. 절대경로와 상위 디렉터리 접근을 허용하며 실제 OS/filesystem 권한이 최종 경계다.

5. **코드 도구**
   - `code_index`와 `code_search`를 모델용 도구로 둔다.
   - `code_index`는 요청한 root의 Python source를 직접 스캔하여 AST 기반 compact structural index를 현재 프로세스 메모리에 만든다.
   - index는 imports, classes/methods, function signatures, routes, registered tool names, config constants, tests 같은 구조 정보를 담는다.
   - source code 전체 본문을 별도 파일로 복제하지 않는다.
   - index를 디스크에 persistent file로 저장하지 않는다.
   - `code_search`는 현재 in-memory structural index를 검색하고, index가 없거나 요청 root가 달라지면 현재 source에서 자동 재생성할 수 있다.
   - 같은 root의 source가 변경되었을 때 기존 index를 조용히 자동 교체하지 않는다. 필요하면 모델이 `code_index`를 다시 호출해 명시적으로 갱신한다.

6. **터미널**
   - `terminal_command`는 owner 기준 application-level 인위적 권한 제한을 두지 않는다.
   - 실제 OS/shell/filesystem/registry/process 권한이 최종 경계다.
   - 실제 실행 실패는 그대로 실패로 반환한다.

## 2. 모델과 Framework 책임 분리

### 모델이 결정한다
- memory를 조회할 필요가 있는지
- 어떤 개념을 memory에서 찾을지
- `node_lookup`에 사용할 lexical query들
- lookup 후보 중 어떤 node를 focus로 recall할지
- 추가로 어떤 node를 recall할지
- 어떤 일반 tool을 사용할지
- tool 결과를 어떻게 해석할지
- 최종 답변 내용
- graph node의 의미와 이름
- graph edge/relation의 의미와 이름
- 어떤 기억을 새로 만들지/수정할지
- 현재 턴 표현을 어떤 개념으로 정규화/일반화할지

### Framework가 강제한다
- single-agent loop의 action schema
- action/tool별 구조 계약
- authentication/account role
- graph ownership
- recall scope
- mutation scope
- DB transaction
- tool 실제 실행
- lexical lookup의 실제 DB 조회
- focus node에서 사용자 anchor까지의 구조적 경로 계산
- current-turn file path provenance
- inspection tool structural progress contract
- 성공/실패 상태
- fixed answer 불변성
- memory mutation 성공 전 answer 비공개

> 의미 판단은 모델, 실행 계약은 Framework.

## 3. 의미 휴리스틱 금지

다음과 같은 방식으로 의미를 판단하지 않는다.

```text
if text contains ...
if filename contains ... then route ...
if answer contains correction phrase ...
```

문자열 규칙으로 tool route, correction 의도, 사람/AI/정체성, 관계 의미, memory 의미를 결정하지 않는다. malformed model output도 의미적으로 자동 교정하지 않는다. Schema 위반은 contract failure로 드러낸다.

단, 모델이 명시적으로 생성한 `node_lookup` query를 실제 `node.name`과 lexical partial match하는 것은 의미 routing이 아니라 검색 연산이므로 허용한다. Framework는 어떤 query를 만들지 결정하지 않는다.

## 4. Sentence_Breaker 제거

Runtime에서 다음을 전부 사용하지 않는다.
- Sentence_Breaker
- Sentence_Breaker DB
- `writable_terms`
- `term_id`
- segmentation 단계와 segmentation fallback

sLLM이 직접 semantic node 이름과 relation을 만든다.

## 5. Turn Lifecycle

Runtime lifecycle은 별도 memory-agent phase를 두지 않는 **single agent loop**다.

```text
User Input
  ↓
Framework Turn Initialization
  └─ canonical user anchor 보장
  ↓
Single Agent Loop
  ├─ answer directly
  ├─ node_lookup
  ├─ recall_memory
  └─ normal work tool
       ↓
       tool result → same agent loop
  ↓
Final Answer Action
  ├─ fixed answer text
  └─ memory_mutations[]
  ↓
Framework fixes answer text
  ↓
Framework executes memory mutation plan
  ↓
Mutation success
  ↓
Release exact fixed answer
```

`Mandatory Memory Discovery`와 별도의 model-driven `Memory Completion`은 runtime turn lifecycle에 존재하지 않는다.

툴이 필요 없는 일반 대화는 모델이 첫 round에 final answer action을 반환할 수 있으므로 **모델 호출 1회**로 완료할 수 있어야 한다.

Fixed answer는 memory mutation 성공 전에는 사용자에게 release하지 않는다.

## 6. One Model Round = One Action

각 model response는 정확히 하나의 top-level action만 표현한다.

Tool action 예:

```json
{"action":"tool","tool":"node_lookup","arguments":{"queries":["MAI","MAI 프로젝트"]}}
```

```json
{"action":"tool","tool":"recall_memory","arguments":{"focus_node_id":17}}
```

일반 work tool도 동일하게 한 round에 하나만 호출한다.

Final answer action은 한 개의 answer action이며, answer와 함께 실행할 memory mutation plan을 구조적으로 포함한다.

```json
{
  "action":"answer",
  "content":"...",
  "memory_mutations":[
    {
      "kind":"write_memory",
      "arguments":{
        "subject":{"kind":"user"},
        "relation":"...",
        "object":{"new_node":{"name":"..."}}
      }
    }
  ]
}
```

`memory_mutations`는 answer action의 commit plan이지 같은 model round의 별도 tool call이 아니다. Framework는 answer text를 먼저 고정한 뒤 이 plan을 실행한다.

Agent loop에는 임의의 global round cap을 두지 않는다. 실제 오류/취소/server shutdown이 발생하거나 모델이 final answer action을 낼 때까지 필요한 만큼 반복할 수 있다.

## 7. Memory Discovery / Recall Contract

### 7.1 Agent-driven memory discovery

Memory discovery는 별도 mandatory model phase가 아니다. `node_lookup`과 `recall_memory`는 single agent loop 안의 구조화된 memory inspection tool이다.

모델이 현재 질문에 memory가 필요하지 않다고 판단하면 첫 round에 바로 answer할 수 있다.

모델이 memory가 필요하다고 판단하면 예를 들어 다음 흐름을 사용할 수 있다.

```text
model -> node_lookup(queries <= 3)
framework -> actual node candidates
model -> recall_memory(focus_node_id)
framework -> one-hop neighborhood + origin path
model -> answer or another tool
```

그래프가 비어 있거나 lookup 결과가 없으면 그 사실을 실제 결과로 반환한다. 성공처럼 보이는 guessed node나 fallback focus를 만들지 않는다.

### 7.2 node_lookup

`node_lookup`은 모델이 만든 최대 3개의 lexical query를 입력으로 받는다.

Framework는 각 query를 user-owned `node.name`에 대해 lexical partial match하고 실제 존재하는 node 후보만 반환한다.

Framework는:
- query의 의미를 해석하지 않는다.
- 동의어를 임의 생성하지 않는다.
- embedding/semantic similarity를 자동 적용하지 않는다.
- 없는 node를 추측해서 만들지 않는다.

Lookup 결과가 현재 agent turn의 candidate set을 전혀 확장하지 못하면 이후 해당 turn에서 `node_lookup`을 schema에서 제거한다. 이는 의미 판단이나 round cap이 아니라 structural no-progress gate다.

### 7.3 Focus selection

lookup 결과의 실제 `node_id` 중 무엇을 focus로 선택할지는 모델이 결정한다.

`recall_memory`는 현재 turn의 실제 lookup candidate에 포함된 user-owned stable `focus_node_id`를 요구한다. 임의 ID나 foreign-user ID는 scope failure다.

### 7.4 One-hop neighborhood

한 번의 `recall_memory`는 focus 기준 **정확히 1 depth neighborhood**만 반환한다.

- focus node
- focus에 직접 들어오는 edge
- focus에서 직접 나가는 edge
- 위 edge들의 반대 endpoint node

recursive multi-depth neighborhood 확장은 하지 않는다. 더 먼 관계는 모델이 추가 `recall_memory`를 호출해서 확인한다.

### 7.5 Canonical user anchor

각 user graph에는 framework가 보장하는 canonical **user anchor node**가 정확히 하나 존재한다.

이 anchor는 recall focus를 강제로 정하기 위한 root가 아니다. 오직:
- user identity endpoint
- origin/path anchor

로 사용한다.

모델이 일반 semantic node와 동일한 이름을 만들었다는 이유로 user anchor로 취급하지 않는다. anchor 여부는 문자열 이름이 아니라 framework-managed structural identity로 구분한다.

### 7.6 Origin path

`recall_memory(focus_node_id)` 결과에는 1-hop neighborhood와 별도로 **focus node에서 canonical user anchor까지 연결되는 하나의 origin path**를 함께 반환한다.

Origin path 계산 규칙:
- 같은 user graph 안에서만 탐색한다.
- edge 방향은 path 탐색 가능 여부를 제한하지 않는다. 즉 구조적 연결성 기준으로 탐색한다.
- 여러 경로가 존재하면 edge 수가 가장 적은 shortest path 하나만 반환한다.
- 반환할 때는 각 edge의 실제 `subject_node_id`, `relation`, `object_node_id` 방향을 그대로 보존한다.
- shortest path가 여러 개로 동률이면 stable ID 기반 deterministic ordering으로 하나를 선택한다.
- 의미 점수나 문자열 휴리스틱으로 경로를 고르지 않는다.
- user anchor까지 연결된 경로가 없으면 `origin_path`는 명시적으로 unavailable/empty 상태를 반환하며 임의 연결을 만들지 않는다.

Origin path는 recursive recall이 아니라 별도 provenance/navigation view다.

### 7.7 Recall scope

recall 결과에는 실제 stable `node_id`, `edge_id`를 포함한다.

현재 턴에서 lookup 후보로만 본 node는 mutation scope에 자동 포함하지 않는다. 기존 memory 수정 가능 scope는 실제 `recall_memory` 결과에 포함된 node/edge로 제한한다.

현재 final memory plan에서 새로 생성된 node는 이후 같은 plan의 Framework 실행 scope에 포함할 수 있다. 모델이 final plan 작성 시 알 수 없는 새 stable ID를 추측해서 참조할 수는 없다.

## 8. Answer Contract

모델이 유효한 final `answer` action을 반환한 순간 `content`가 fixed answer가 된다.

Final answer action은 반드시 최소 1개의 `memory_mutations`를 포함한다.

Framework 실행 순서:

```text
1. structured final action validation
2. answer content 고정
3. memory mutation plan 실행
4. 모든 필수 mutation 성공 확인
5. 고정된 answer content 그대로 release
```

Mutation 단계에서 답변 수정, 재생성, helper 교체, 성공 fallback 생성을 금지한다. Mutation이 실패하면 fixed answer를 성공 응답으로 release하지 않고 실제 실패를 드러낸다.

## 9. Semantic Graph Contract

### Node
```text
node_id       Framework-generated stable ID
user_id       Framework-enforced owner
name          model-authored semantic name
created_at
updated_at
```

일반 node의 `name`은 모델이 정한다. Canonical user anchor의 구조적 identity는 Framework가 별도 mapping으로 관리하며 이름 문자열 비교로 판별하지 않는다.

### Edge
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
정확한 character span은 강제하지 않는다. 각 mutation은 최소 `turn_id`와 source role/context를 기록한다. Node name/relation은 원문의 exact substring일 필요가 없고 모델이 현재 턴 문맥을 기반으로 의미를 일반화할 수 있다.

## 10. Memory Commit Contract

모든 정상 턴에서 최소 1회의 memory mutation이 성공해야 한다.

이 요구는 별도 memory model loop로 구현하지 않는다. Final answer action의 `memory_mutations` 배열이 최소 1개를 구조적으로 요구한다.

허용 mutation:
- `write_memory`
- `revise_memory` (실제 recalled edge가 현재 final schema에 존재할 때만)

Framework는 final answer가 고정된 이후 mutation plan을 순서대로 실행한다. 모든 mutation은 기존 write/revise scope, ownership, transaction 계약을 그대로 사용한다.

별도의 `done` model round는 없다. 계획된 mutation 실행이 성공적으로 끝나면 Framework가 memory status를 `done`으로 확정한다.

## 11. write_memory Contract

모델은 기존 recalled node를 endpoint로 사용하거나 현재 턴 문맥에서 새 node를 만들 수 있으며 relation을 직접 정의한다.

Final plan 예:

```json
{
  "kind":"write_memory",
  "arguments":{
    "subject":{"kind":"user"},
    "relation":"좋아한다",
    "object":{"new_node":{"name":"로봇공학"}}
  }
}
```

`{"kind":"user"}`는 canonical user anchor를 구조적으로 가리킨다. Framework는 의미의 옳고 그름을 문자열로 검증하지 않고 scope/ownership/transaction만 강제한다.

## 12. revise_memory Contract

`revise_memory`는 현재 턴에서 실제 recall된 graph edge만 final answer schema에 노출한다. 실행 시 기존 `ReviseMemoryScope`의 ownership/scope 검증을 그대로 적용한다.

현재 final plan 실행 중 새로 만들어진 node/edge는 Framework 내부 실행 scope에 포함될 수 있지만, 모델은 final action 작성 시 아직 존재하지 않는 stable ID를 추측해서 참조할 수 없다.

임의 ID, ownership violation, DB collision은 그대로 실패한다. 자동 병합이나 문자열 기반 보정을 하지 않는다.

## 13. Tool Contract

모든 모델용 work tool은 최소 다음을 가진다.

```text
name
description
work_kind
JSON input schema
execution handler
```

`work_kind`는 구조적으로 다음 중 하나다.
- `inspection`
- `action`

Inspection tool은 `progress_keys(result)`를 반드시 제공한다. 새 structural progress key를 하나도 추가하지 못한 성공 실행 이후에는 현재 turn의 다음 model schema에서 해당 inspection tool을 제거한다.

Action tool은 동일 result identity로 반복 가능 여부를 판단하지 않는다. 정상적인 반복 write/command가 의미 있을 수 있기 때문이다.

Framework는 사용자 문장을 보고 tool route를 의미적으로 결정하지 않는다.

## 14. File Tool Contract

필수 모델용 도구:
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

Owner 계정에서는 application-level workspace confinement를 두지 않는다.

기존 파일을 읽거나 수정/삭제/다운로드하는 action은 current-turn path provenance에 포함된 실제 path만 사용할 수 있다.

Path provenance source:
- 인증된 upload attachment
- `file_tree`
- `file_search`
- `file_text_search`
- `code_index`
- `code_search`
- 성공한 `file_create`

발견된 path만 다음 round의 path enum에 노출한다. `file_create`는 새 path를 만드는 도구이므로 provenance 없이 새 path를 지정할 수 있으며 성공 후 그 path를 provenance에 추가한다. `file_delete` 성공 후에는 해당 path를 provenance에서 제거한다.

## 15. Code Tool Contract

모델용 코드 도구는 `code_index`와 `code_search`를 둔다.

### code_index
- 요청 root의 Python 파일을 직접 읽고 AST를 파싱한다.
- compact in-memory structural map을 만든다.
- 최소 구조 정보는 imports, classes/methods, function signatures, routes, registered tool names, config constants, tests다.
- index state는 현재 Python 프로세스 메모리에만 존재한다.
- index를 별도 파일/DB/cache로 persistent하게 저장하지 않는다.
- source code 전체 본문을 별도 index 저장소에 복제하지 않는다.
- parse 실패는 `parse_errors`에 실제 경로/오류와 함께 드러낸다.

### code_search
- 현재 in-memory code index의 구조 정보를 검색한다.
- index가 없거나 요청 root가 기존 indexed root와 다르면 현재 source에서 `code_index`를 자동 실행할 수 있다.
- 같은 root의 source 변경은 자동 감지/자동 rebuild하지 않는다. 모델이 최신 구조가 필요하면 `code_index`를 다시 호출한다.
- 검색 결과는 실제 indexed file/symbol 구조를 반환하고 모델은 필요하면 `file_read`로 선택한 source를 상세 조회한다.
- result file path를 inspection progress key로 사용한다.

이 구조는 옛 MK4의 compact repository map 방식을 계승하되 index 파일을 디스크에 저장하거나 실패를 fallback으로 숨기지 않는다.

## 16. Document / Image Contract

- `document_read`: PDF/DOCX만 명시적으로 지원한다.
- `image_analyze`: 별도 configured Ollama vision model로 이미지 분석한다.
- vision model은 일반 chat model과 분리한다.
- 기존 파일 path provenance를 만족해야 한다.

```env
MAI_OLLAMA_MODEL=...
MAI_OLLAMA_IMAGE_MODEL=...
```

## 17. Terminal Contract

`terminal_command`는 owner 기준 application-level permission boundary를 추가하지 않는다. Workspace 밖, registry, startup 등의 이유로 Framework가 의미적으로 차단하지 않는다. OS/shell의 실제 권한과 실행 결과가 기준이다.

## 18. Authentication / Owner Contract

Owner는 최소 다음 권한을 가진다.
- unrestricted file path discovery at application level
- discovered/current-turn file CRUD at application level
- unrestricted terminal invocation at application level

실제 OS 권한은 우회하지 않는다.

## 19. Tailscale Hosting Contract

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

Tailscale/Funnel 오류는 숨기지 않는다.

## 20. Error Contract

실패는 실패로 남긴다.

```text
schema 위반       -> contract failure
없는 ID           -> scope failure
ownership 위반    -> authorization/scope failure
DB collision      -> database failure
tool 실패         -> tool failure
Ollama 실패       -> model failure
Tailscale 실패    -> hosting failure
```

금지:
- 오류 문자열 비교 후 성공 처리
- 본체 실패를 fallback으로 성공처럼 처리
- guessed path/ID 자동 대입
- malformed tool call 자동 교정
- helper가 본체 response contract를 대신 만족

## 21. Logging Contract

Lifecycle progress log는 최소 다음을 구분할 수 있어야 한다.

```text
turn_initialization
agent round N
selected action/tool
tool started/completed
memory_mutation
turn completed/failed
```

사용자 message 본문, tool argument 전체, tool result 전체를 lifecycle progress log에 강제로 노출하지 않는다. 오류는 숨기지 않는다.

## 22. Architecture History / Current Direction

초기 구현 순서는 다음 계층을 독립 PR로 만들었다.

```text
semantic graph
one-depth recall
user anchor + lookup + origin path
mandatory memory discovery
write/revise memory
mandatory memory completion
work/tool loop
runtime/UI/file/document/terminal/code/web tools
```

이후 로컬 small-model latency와 tool-loop 종료 문제를 실제 실행으로 확인한 결과, runtime lifecycle은 다음 방향으로 정리한다.

```text
single agent loop
+ agent-driven memory lookup/recall
+ final answer embedded memory plan
+ framework-only post-answer mutation execution
+ inspection structural progress gating
+ current-turn file path provenance
```

과거 mandatory discovery/completion 모듈의 존재 여부가 현재 runtime contract를 바꾸지 않는다. Runtime wiring과 `AgentLifecycle`은 본 문서의 single-agent lifecycle을 따라야 한다.

각 코드 변경은 `main`에서 새 branch를 만들고 독립 PR로 구현하며, 로컬 테스트와 실제 실행 검증 후 사용자가 수동으로 main에 merge한다.
