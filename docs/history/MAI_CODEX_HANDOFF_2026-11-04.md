# 2026-11-04 작업·장애 인계 기록

이 문서는 `94d767537af05c29d04d82fc923e5cf36bdcaa69`까지의 과거 작업과 사용자 제공 로그 분석을 보존한다. 현재 코드·지시를 대체하지 않는다. 중복된 제품·구현 설명과 오래된 작업 지시는 기준 문서로 통합했다.

현재 구조는 [실행 계약](../architecture.md), [기억](../memory.md), [검증](../verifier.md), [도구 결과](../TOOL_RESULT_PAGINATION.md), [설치](../../setup.md), [Portable](../../portable/README.md)을 읽는다. 작업 규칙은 루트 [AGENTS.md](../../AGENTS.md)를 우선한다. dev에서 작업 브랜치를 만들어 검증 후 dev에 병합하며 main 반영은 사용자가 진행한다.

당시 사용자 보고 장비는 i5-12400F / RAM 16GB / RX 6800 XT 16GB였다. 실제 GPU 적재·메모리·추론 속도를 측정한 수치는 아니다. 로그 원본은 이 문서에 포함되지 않는다.

아래 PR·테스트·지연 수치는 **당시 확인 결과**이며 이후 작업 완료나 성능 개선을 뜻하지 않는다.

## 4. 역사: 계승한 문제의식과 현재 구현을 분리할 것

### 4.1 Machi/MK와 MAI의 관계

Machi(MK 계열)는 장기기억·사용자 모델·증거 누적·정정·충돌·그래프 사고를 실험해 온 앞선 프로젝트다. 대화 기록에는 MK4/MK5/MK6의 이름과 역할을 재정리한 시점들이 있다. **숫자만 보고 동일한 아키텍처가 계속 유지됐다고 해석하면 안 된다.**

현재 MAI에서 중요한 구분은 다음이다.

- **실사용 MAI:** LLM이 판단·도구 오케스트레이션을 담당하고 그래프가 장기기억과 retrieval을 담당하는 로컬 에이전트.
- **graph-first cognition 연구:** 그래프 자체의 활성화·사고·결론 구성과 LLM의 표면화를 분리하려던 과거/장기 연구 방향. 현재 production이 이미 이 방식으로 사고한다고 말하면 안 된다.

과거의 SurfaceFrame, TurnCycle, 모델 관리형 기억 쓰기/수정 API, edge weight=0, 임베딩 기반 의미 검색 같은 구상을 현재 계약으로 가져오지 않는다. 현재 코드가 실제로 호출하는 경로를 먼저 본다. 과거 변경의 철회 이유가 기록에서 확정되지 않으면 임의로 이유를 만들어 쓰지 않는다.

### 4.2 Git으로 확인한 큰 전환

| 시점 | 확인되는 변화 | 현재 이어받을 의미 |
|---|---|---|
| 2026-08-20 | 최초 커밋 `e634815`: `start : Machi_MK5 가져오기` | Machi 코드/문제의식을 출발점으로 삼음 |
| 08-21~22 | 최소 코어·계약·메모리·UI 재구성, 여러 기억 lifecycle 실험 | 이 시기 모든 인터페이스가 현재 남아 있는 것은 아님 |
| 08-27 | `72a3507`, PR #66: Ollama-native runtime scaffold로 재구성 | 현재 native model/tool loop의 큰 기반 |
| 08-28 | `b388c4c`, PR #76: vector recall을 exact hash + SQLite FTS5로 교체 | 현재 lexical 검색; 임베딩 엔진이 아님 |
| 08-29 | `b4d8b29`: production post-response fact extractor | 응답 완료 뒤 사실 추출 |
| 10-06 | Fact-only recall·admission 관련 변경 | 내용 중심 Fact 반환과 원문/기억 구분 |
| 10-23 | PR #283: lazy native activation | catalog → tool_manual → 활성화 schema |
| 10-28~29 | 원격 native PC 도구 통합, Windows 실행 개선, 이전 승인 대화의 verifier 근거화, planner 제거 | 예전 remote 전용 도구/preflight 설명을 현재로 오인하지 말 것 |
| 11-02 | PR #395~397: 수량 근거·도구 개수 상한 제거·CSV 집계 | reviewer가 근거 없는 대체 수량을 만들지 않도록 함 |
| 11-03 | PR #398~401: 가정 재검토·출처 인용·선택적 metadata 처리·#400 되돌림 | 속도 회귀에 민감; 아래 최근 이력 참고 |
| 11-04 | PR #402~407 | 현재 인계의 직접 대상 |

이 표는 전체 커밋 분석서가 아니다. 재구성에 영향을 주는 주요 전환과 최근 작업의 연결을 요약했다.

## 12. 최근 PR과 현재 진행 상태

아래 PR은 작성 시점 GitHub에서 모두 dev 병합 완료를 확인했다. #400은 이후 #401에서 되돌렸다.

| PR | 내용 | 인계 시 알아둘 점 |
|---|---|---|
| [#397](https://github.com/FoggyCaligo/MAI_MyAI_sllm/pull/397) | deterministic CSV 집계·provenance | 단순 CSV 수량을 reviewer가 잘못 반박하는 문제에 대응 |
| [#398](https://github.com/FoggyCaligo/MAI_MyAI_sllm/pull/398) | 출처 기반 반려·가정 재검토 | verifier 피드백도 새 사실이 아님 |
| [#399](https://github.com/FoggyCaligo/MAI_MyAI_sllm/pull/399) | evidence_refs를 선택적 metadata로 처리 | 인용 metadata 오류만으로 응답 중단하지 않음 |
| [#400](https://github.com/FoggyCaligo/MAI_MyAI_sllm/pull/400) | 도구 용도/입력/반환 정보로 생성 전 오류 예방 | #401에서 revert됨. 무심코 다시 적용하지 말 것 |
| [#401](https://github.com/FoggyCaligo/MAI_MyAI_sllm/pull/401) | #400 되돌림 | 사용자가 이후 훨씬 빨라졌다고 보고함. 통제된 벤치마크는 아님 |
| [#402](https://github.com/FoggyCaligo/MAI_MyAI_sllm/pull/402) | verifier 중복 문구 통합 | 동등한 의미를 경량화하려는 변경 |
| [#403](https://github.com/FoggyCaligo/MAI_MyAI_sllm/pull/403) | 숫자검증 통합 + 원문 발췌/재열람 | 별도 숫자 LLM 제거, 본 모델의 근거 선택에 의존 |
| [#404](https://github.com/FoggyCaligo/MAI_MyAI_sllm/pull/404) | 전체 테스트 수정 + 짧은 음성 허용 | 등록 3초와 일반 입력 기준을 분리 |
| [#405](https://github.com/FoggyCaligo/MAI_MyAI_sllm/pull/405) | 중복/중첩 원문 읽기 dedup·무진행 안내 | 같은 batch 중복으로 즉시 중단하던 경로 수정 |
| [#406](https://github.com/FoggyCaligo/MAI_MyAI_sllm/pull/406) | 코드 기준 문서 13개 정합성 수정 | 이전 명세의 planner·추출 단계·기본값 설명을 그대로 믿지 말 것 |
| [#407](https://github.com/FoggyCaligo/MAI_MyAI_sllm/pull/407) | 읽기/판정 union schema·미열람 범위·형식 진단 | 최신 수정. 실제 로컬 재검증이 다음 확인 사항 |

현재 dev merge commit은 `94d767537af05c29d04d82fc923e5cf36bdcaa69`다. #407 구현 commit `9831f1202f53323bb567092e1c9cf30ce47518f2`와 검사한 코드 tree는 같으며 tree SHA는 `9013ef4347ceff1817dc978109bf71a2ccc71b1a`다.

이 문서 작성은 저장소 코드 변경을 수행하는 작업이 아니다. 새로운 handoff 파일을 만들었으며, 그 파일을 저장소에 넣는 PR을 생성하거나 #407 이후 추가 기능을 구현한 것은 아니다.

## 13. 최신 오류와 속도 분석: 확인한 것과 모르는 것

분석 파일은 사용자가 제공한 `log(20261104-035842).sqlite3`다. 해당 파일은 이전 날짜의 runs도 포함한 누적 감사 DB다. 분석에서는 read-only로 열었다. 파일 자체는 이 Markdown에 포함돼 있지 않다. 필요하면 사용자에게 해당 파일 또는 이후 새 로그를 받아야 한다. 다른 Codex 환경에 앞선 작업의 임시 경로가 존재한다고 가정하지 않는다.

### 13.1 세 번의 오류 흐름

| run_id | 실패 | 코드/로그에서 확인한 의미 |
|---|---|---|
| T1DTd_18FouRtgSQhuvJ5bjW | original read adds no new source characters | #405 이전 중복 범위 처리 문제 |
| zwXbwKtkzSL2TnNcuQDMoLCx | EvidenceReviewStep ValidationError | non-empty read_requests와 review를 동시에 반환; #407 대상 |
| b9BkCYrvU1yj4770mvav8dYo | original read progress budget exhausted | #405 이후에도 이미 제공한 구간 반복; #407 대상 |

마지막 오류는 `tool:7`, offset 7,488 / limit 7,488을 반복했고, `already_provided` 안내 뒤 연속 무진행 2회로 종료했다. 해당 출처는 연합뉴스 목록의 도구 반환 원문 75,671자였다. 전체 source가 11,561자였다고 혼동하면 안 된다. 11,561자는 별도의 Google News 결과였다.

후보 답변 자체에도 추가 검증이 필요한 내용이 있었으므로, 이 오류를 없애려고 마지막 초안을 검증 성공으로 내보내면 안 된다. 오류와 미검증 초안은 구분해 보존한다.

### 13.2 시간 수치

다음 시간은 audit timestamp의 **candidate 생성 → verifier 기록 또는 오류** 구간을 합산한 값이다. 개별 Ollama 요청의 순수 GPU 추론 시간 측정은 아니다.

UI의 model_rounds는 main AgentLoop 라운드 수다. verifier 재호출과 기억 추출 호출 수를 포함하지 않으므로 라운드가 적어도 검증에서 오래 걸릴 수 있다.

| 실행 | 전체 | 검증 구간 합계 | 전체 중 비중 |
|---|---:|---:|---:|
| 11/03 VR 설명 lFXP… | 439.880초 | 82.909초 | 18.8% |
| 11/03 VR 비교 P4SG… | 642.377초 | 492.011초 | 76.6% |
| 11/04 첫 뉴스 QeP… | 952.785초 | 684.721초 | 71.9% |
| 11/04 형식 오류 zwX… | 381.312초 | 116.902초 | 30.7% |
| 11/04 반복 읽기 오류 b9Bk… | 729.636초 | 496.931초 | 68.1% |

마지막 실행은 초안까지 약 232.705초, 이후 오류까지 검증 약 496.931초다. 최근 두 뉴스 실행의 retain_evidence 호출은 0회였다. 참조만 남은 근거를 reviewer가 재열람하고, 같은 범위를 반복하는 비용이 관측됐다.

### 13.3 인과관계의 한계

- 첫 뉴스 실행은 #403 병합 전 시작했는데도 검증에 약 11분 25초가 걸렸다. 지연 전체를 새 압축 기능 하나로 설명할 수 없다.
- 어제의 VR 비교도 검증 비중이 76.6%였다. “어제는 언제나 빠르고 오늘만 느림”이라는 비교는 로그와 맞지 않는다.
- 질문·검색량·출력량·교정 횟수가 다르므로 동일 조건 전후 벤치마크가 아니다.
- 일부 web_search는 DDGS의 `No results found`로 실제 실패했다. 검색 실패·재조사·직렬 실행 시간도 추가 요소다. 이를 성공처럼 반환하는 우회는 하지 않았다.
- 큰 도구 원문이 20만 자라고 그 전체가 main 입력에 들어갔다고 계산하면 안 된다. 최초 입력은 bounded page이며 원문과 모델용 view를 나눠 측정해야 한다.
- 현재 로그만으로 GPU 적재 변화, RAM 압박, Ollama 재로딩, prefix cache, token/sec 저하를 확정하지 못했다.

수정 후 속도가 실제로 줄었다는 결과는 아직 없다. #407은 실패를 유발한 구조를 수정하고 지시를 보강한 것이며 모델의 준수율과 실제 지연 개선은 후속 로그로 확인해야 한다.

## 14. 다음 작업의 우선순위와 사용자 확인이 필요한 선택

### 우선 확인할 운영 사실

1. 사용자 PC가 #407 포함 dev를 pull하고 서버를 재시작했는지 확인한다. GitHub merge와 로컬 실행 버전은 다르다.
2. 같은 뉴스 요청 또는 사용자가 지정한 재현 요청의 새 로그를 읽는다.
3. 두 종류의 오류가 사라졌는지, reviewer read 호출·중복 요청·형식 재시도가 어떻게 바뀌었는지 본다.
4. 본 모델이 retain_evidence를 실제 호출하는지 확인한다. prompt 보강만으로 해결됐다고 가정하지 않는다.
5. 성공 여부와 별도로 candidate·최종 답변의 사실 근거와 범위를 확인한다. 일반적인 지식으로 원문 관측을 대체하지 않는다.
6. main·tool·verifier·memory 대기 시간을 구분한다. 순수 추론 속도가 필요하면 Ollama stdout의 request id, load/prompt_eval/eval duration/count를 함께 받는다.

### 구현 후보이나 자동으로 결정하면 안 되는 것

| 선택 | 먼저 결정해야 할 이유 |
|---|---|
| evidence 선택을 필수 gate로 강제 | 선택할 필요가 없는 도구 사용/답변도 있고 추가 라운드·교정이 늘 수 있음 |
| 선택이 없을 때 원문을 자동 보존 | 현재 “선택 발췌만 전달” 계약과 비용이 바뀜 |
| 별도 요약/선별 LLM 또는 planner 추가 | 호출 수와 판단 책임이 바뀜 |
| verifier 범위 축소/생략 또는 모델 교체 | 오류·정확성·지연의 교환 관계를 사용자가 결정해야 함 |
| 전체 history 중복 제거/새 입력 한도 | 사용자 요청한 최근 대화 범위·근거 역할을 유지할 설계 필요 |
| 기억 출처 연결/owner 격리/정정 엔진 | 데이터 모델·기존 DB migration·의미 정책의 변경 |
| 미사용 설정 키 삭제/실제 연결 | 호환성과 사용자의 기존 .env 영향 확인 필요 |

이 목록은 중요도를 설명하는 것이지 새 기능 구현 승인 목록이 아니다. 사용자 요청 범위의 재현·계측·명확한 계약 오류 수정은 진행하되 구조적 대안이 갈리면 먼저 질문한다.

## 15. 검증과 재현 절차

### 기본 설치와 검사

해당 Python 가상환경을 활성화한 뒤 실행한다. 아래는 플랫폼 공통 명령이며 모델·브라우저·음성의 실제 준비는 setup 문서를 따른다.

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
git diff --check
```

`jsonschema`는 #407의 생성용 JSON 스키마 회귀 검사에 필요한 **dev 의존성**이다. 일반 실행에 별도 외부 검증 서비스를 추가한 것이 아니다.

이번 원문 재열람/형식 수정의 집중 검사:

```bash
python -m pytest -q tests/test_reviewer_read_contract.py tests/test_evidence_compaction.py tests/test_final_grounding_verifier.py tests/test_verifier_split_contract.py tests/test_tool_results.py
```

전체 코드 감사는 필요할 때 `scripts/audit_references.py`, `scripts/audit_codebase.py`를 사용한다. 정적 미참조 후보는 삭제 확정이 아니며, 테스트 직접 import 여부는 coverage가 아니다.

### 최근 검증이 증명한 범위

- #407 코드 tree에서 전체 `482 passed, 13 skipped`를 확인했다.
- 읽기/판정 동시 응답·빈 응답이 생성 JSON 스키마와 Pydantic에서 모두 거부됨을 검사했다.
- combined numeric의 필수 판정, 출처·offset·limit 제약, 7,488 반복 구간 제외, 75,671자 source의 남은 범위를 검사했다.
- 전부 제공된 source의 판정 branch, retained 범위의 제공 완료 계산, 형식 진단 전달·반려 유지·재시도 소진 시 실패를 검사했다.
- 위 검사에는 가짜 reviewer와 JSON Schema validator를 사용했다. 실제 Ornith가 항상 올바른 근거를 선택하거나 새 schema를 잘 따르는지는 증명하지 않는다.
- Windows 전용 13개, 실제 모델 추론, 실제 GPU·마이크·Tailscale/원격/Portable·브라우저 E2E는 별도 사용자 환경 검증이 필요하다.

임시 테스트 환경 경로를 사용자 PC의 필수 구성처럼 안내하지 않는다. 앞선 ChatGPT 작업 공간의 test-deps나 별도 worktree는 Codex 세션에 자동 이전되지 않는다.
