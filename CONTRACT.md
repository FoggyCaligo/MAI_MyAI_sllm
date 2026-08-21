# MAI sLLM Runtime Contract

이 문서는 `MAI_MyAI_sllm`의 최상위 실행 계약이다. 구현은 이 계약을 따라야 하며 helper, fallback, compatibility layer가 본 계약을 우회해서는 안 된다.

## 1. 제품 요구사항

1. **Tailscale 외부 접속**
   - Tailscale이 생성한 URL을 통해 외부에서 접속 가능해야 한다.
   - 서버는 로컬 바인딩을 유지하고 Tailscale Serve/Funnel이 외부 공개를 담당할 수 있다.
   - Tailscale 설정 실패는 숨기지 않는다.

2. **UI**
   - 사용자에게 보이는 UI는 기존 MACHI/MK4 형식을 그대로 따른다.
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
   - `code_search`만 유지한다.
   - `code_index`는 모델 도구뿐 아니라 내부 구현에서도 만들지 않는다.
   - persistent/in-memory 사전 코드 인덱스 계층을 두지 않는다.
   - `code_search`는 필요할 때 직접 파일 구조와 소스를 탐색한다.

6. **터미널**
   - `terminal_command`는 owner 기준 application-level 인위적 권한 제한을 두지 않는다.
   - 실제 OS/shell/filesystem/registry/process 권한이 최종 경계다.
   - 실제 실행 실패는 그대로 실패로 반환한다.

## 2. 모델과 Framework 책임 분리

### 모델이 결정한다
- 무엇을 recall할지
- 어떤 tool을 사용할지
- tool 결과를 어떻게 해석할지
- 최종 답변 내용
- graph node의 의미와 이름
- graph edge/relation의 의미와 이름
- 어떤 기억을 새로 만들지/수정할지
- 현재 턴 표현을 어떤 개념으로 정규화/일반화할지

### Framework가 강제한다
- 현재 phase
- phase별 허용 action/tool
- JSON schema
- authentication/account role
- graph ownership
- recall scope
- mutation scope
- DB transaction
- tool 실제 실행
- 성공/실패 상태
- fixed answer 불변성

> 의미 판단은 모델, 실행 계약은 Framework.

## 3. 의미 휴리스틱 금지

다음과 같은 방식으로 의미를 판단하지 않는다.

```text
if text contains ...
if filename contains ... then route ...
if answer contains correction phrase ...
```

문자열 규칙으로 tool route, correction 의도, 사람/AI/정체성, 관계 의미, memory 의미를 결정하지 않는다. malformed model output도 의미적으로 자동 교정하지 않는다. Schema 위반은 contract failure로 드러낸다.

## 4. Sentence_Breaker 제거

Runtime에서 다음을 전부 사용하지 않는다.
- Sentence_Breaker
- Sentence_Breaker DB
- `writable_terms`
- `term_id`
- segmentation 단계와 segmentation fallback

sLLM이 직접 semantic node 이름과 relation을 만든다.

## 5. Turn Lifecycle

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

Fixed answer는 memory commit 성공 전에는 사용자에게 release하지 않는다.

## 6. One Model Round = One Action

각 model response는 정확히 하나의 action만 표현한다.

```json
{"action":"tool","tool":"recall_memory","arguments":{}}
```

또는

```json
{"action":"answer","content":"..."}
```

또는 해당 phase에서 허용된 경우

```json
{"action":"done"}
```

한 응답에 여러 tool call, tool+answer를 함께 넣지 않는다.

Agent loop에는 임의의 global round cap을 두지 않는다. 모델이 terminal action을 내거나 실제 오류/취소/server shutdown이 발생할 때까지 필요한 만큼 반복할 수 있다.

## 7. Recall Contract

- 일반 작업 전에 `recall_memory`를 최소 1회 성공해야 한다.
- 한 번의 recall은 focus 기준 **1 depth**만 반환한다.
- recursive multi-depth expansion은 하지 않는다.
- 더 먼 관계는 추가 `recall_memory` 호출로 확인한다.
- recall 결과에는 실제 stable `node_id`, `edge_id`를 포함한다.
- 기존 memory 수정은 현재 턴에서 recall되었거나 현재 턴에서 생성된 실제 ID만 사용할 수 있다.

## 8. Answer Contract

모델이 `answer` action을 반환한 순간 텍스트가 fixed answer draft가 된다. 이후 memory phase에서 답변 수정, 재생성, helper 교체, 성공 fallback 생성을 금지한다. Memory phase 성공 시 fixed answer를 그대로 release하고, 실패하면 실패를 드러낸다.

## 9. Semantic Graph Contract

### Node
```text
node_id       Framework-generated stable ID
user_id       Framework-enforced owner
name          model-authored semantic name
created_at
updated_at
```

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

첫 memory round에는 `done`을 schema에 노출하지 않는다.

허용 mutation:
- `write_memory`
- `revise_memory` (수정 가능한 recalled/created 대상이 있을 때만)

최소 1회의 mutation 성공 이후에만 `done`을 노출한다.

```text
memory start
→ write/revise required
→ mutation success
→ write/revise/done
→ done
```

## 11. write_memory Contract

모델은 기존 recalled node를 endpoint로 사용하거나 현재 턴 문맥에서 새 node를 만들 수 있으며 relation을 직접 정의한다.

예:
```json
{
  "action":"tool",
  "tool":"write_memory",
  "arguments":{
    "subject":{"kind":"user"},
    "relation":"좋아한다",
    "object":{"new_node":{"name":"로봇공학"}}
  }
}
```

Framework는 의미의 옳고 그름을 문자열로 검증하지 않고 scope/ownership/transaction만 강제한다.

## 12. revise_memory Contract

`revise_memory`는 현재 턴에서 recall되었거나 생성된 graph scope만 수정한다. 현재 scope의 실제 ID만 schema에 노출한다. 임의 ID, ownership violation, DB collision은 그대로 실패한다. 자동 병합이나 문자열 기반 보정을 하지 않는다.

## 13. Tool Contract

모든 모델용 tool은 최소 다음을 가진다.

```text
name
description
JSON input schema
execution handler
```

모델은 structured action으로 tool을 선택하며 Framework가 사용자 문장을 보고 의미적으로 route하지 않는다.

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

## 15. Code Search Contract

모델용 코드 도구는 `code_search` 하나만 둔다. `code_index`는 외부/내부 모두 금지한다. `code_search`는 필요할 때 직접 파일 tree/source를 탐색한다.

## 16. Document / Image Contract

- `document_read`: 최소 PDF/DOCX text extraction.
- `image_analyze`: 이미지 metadata와 별도 configured Ollama vision model 분석.
- vision model은 일반 chat model과 분리한다.

```env
MAI_OLLAMA_MODEL=qwen...
MAI_OLLAMA_IMAGE_MODEL=...
```

## 17. Terminal Contract

`terminal_command`는 owner 기준 application-level permission boundary를 추가하지 않는다. Workspace 밖, registry, startup 등의 이유로 Framework가 의미적으로 차단하지 않는다. OS/shell의 실제 권한과 실행 결과가 기준이다.

## 18. Authentication / Owner Contract

Owner는 최소 다음 권한을 가진다.
- unrestricted file path access at application level
- unrestricted file CRUD at application level
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

로그는 사람이 읽기 쉽게 최소화한다. 정상 chat은 가능한 한 한 줄 summary를 사용한다.

```text
[MAI] chat | recall=1.2s | answer=2.3s | memory=0.8s | tools=3
```

세부 diagnostics는 API/debug data로 유지할 수 있다. 오류는 숨기지 않는다.

## 22. Implementation Order

계약 merge 이후 각 단계는 `main`에서 새 branch를 만들고 독립 PR로 구현한다.

```text
1. semantic graph storage
2. one-depth recall
3. write_memory
4. revise_memory
5. memory completion contract
6. work/tool agent loop
7. MK4 UI + upload + Tailscale hosting
8. file discovery/read
9. file CRUD/download
10. document/image
11. terminal
12. code_search
13. web/current-information tools
```

각 단계는 로컬 테스트와 실제 실행 검증 후 사용자가 수동으로 main에 merge한다.
