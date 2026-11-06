# Agent Skills: 툴 선택과 사용 계약

MAI는 공개 Agent Skills 형식의 `skills/<name>/SKILL.md` 문서를 사용한다. 기본 실행 모델은 이에 종속되지 않는다. Ollama 또는 llama.cpp의 네이티브 툴 호출을 그대로 사용하며, 기존 `ToolRegistry`가 입력 검증·권한·실행을 담당한다.

## 순서

1. 실행 시 현재 계정에 등록된 툴의 `category` 메타데이터와 스킬 `metadata.tool-category`를 정확히 연결한다. 추측성 문자열 분류는 하지 않는다. 대응 스킬이 없거나 중복되면 명시적으로 실패한다.
2. 첫 모델 요청에는 현재 사용 가능한 스킬의 **이름과 짧은 설명** 및 `skill_activate` 스키마만 제공한다.
3. 모델이 `skill_activate({"skills": [...]})`를 실행하면 선택된 `SKILL.md`의 작업 지침과 해당 분류에 속한 툴 **이름만** 다음 모델 요청의 별도 시스템 문맥으로 제공한다.
4. 모델이 `skill_tools({"tools": [...]})`로 구체적인 툴을 선택하면 해당 툴의 **정확한 네이티브 입력 스키마와 선택된 툴의 런타임 계약만** 다음 모델 요청에 노출한다. 선택한 스킬이 허용하지 않은 툴 활성화는 실패한다.
5. 선택된 네이티브 툴만 기존 `ToolRegistry.invoke`를 통해 실행한다. 호출 인자는 assistant의 native tool_call에, 실제 실행 결과는 tool 메시지에 기록한다. 모델의 tool-call thinking이나 부수적 설명, 스킬 본문은 **이전 툴 결과에 넣지 않는다**.

스킬·툴 활성화 확인 메시지는 스킬 이름 또는 툴 이름의 성공 여부만 담는다. 사용법이나 시스템 프롬프트는 도구 결과로 반환하지 않는다. 내부 활성화 메시지는 기억 추출과 Verifier의 근거 목록에서 제외한다. 실제 도구 실행 결과의 실패 및 에러는 유지한다.

## 파일 작성

```yaml
---
name: example
description: Short discovery description for the model
metadata:
  tool-category: example
---
# Example skill

Instructions used after the skill is activated.
```

스킬 파일은 외부 실행 권한을 부여하지 않는다. 모델이 선택할 수 있는 툴은 기존 런타임에 등록됐으며 해당 계정에 허가된 것뿐이다. 툴의 정확한 옵션, 사용법, 예외 규칙은 **툴 선택 이후** 네이티브 스키마와 런타임 컨텍스트에 둔다. 스킬 본문에 같은 분류의 다른 툴별 상세 사용법을 모두 중복하지 않는다. 비신뢰 스킬을 무조건 설치하거나 자동 실행하지 않는다.

분류 없는 명시적 확장 툴은 `general` 스킬에 연결된다. 필요한 경우 툴 등록 코드의 `category`와 `skills/<name>/SKILL.md`의 `metadata.tool-category`를 동시에 추가한다.

## 확인

```bash
python -m pytest -q tests/test_agent_skills.py tests/test_native_lazy_tools.py tests/test_tool_runtime_context.py tests/test_memory_temporal_precedence_policy.py tests/test_memory_admission.py
python -m pytest -q
```

실제 사용 전에는 스킬 사용 전후의 모델 라운드 수, 처리 지연, 툴 호출 정확도, 응답 근거 오류를 비교해야 한다. 두 단계 선택은 초기 모델 호출 한 번을 추가할 수 있으므로 속도 개선을 보장하지 않는다.
