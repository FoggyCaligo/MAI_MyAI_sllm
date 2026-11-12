# Agent Comparison 파일럿 실행 및 자동 채점

> 상태: 최초 구현·서버컴 검증 전 (2026-11-12)

이 폴더는 [전체 벤치마크 안내](../README.md), [테스트 설계](../%ED%85%8C%EC%8A%A4%ED%8A%B8%20%EC%84%A4%EA%B3%84.md), [벤치마크 환경](../%EB%B2%A4%EC%B9%98%EB%A7%88%ED%81%AC%20%ED%99%98%EA%B2%BD.md)의 비교 평가를 위한 초기 실행기다.

## 1. 서버컴에서 먼저 실행: 오프라인 자체 점검

Git Bash에서 MAI 저장소 루트로 이동하고, 본인의 로컬 변경사항을 확인한 뒤 최신 dev를 가져온다. **작업 폴더에 미커밋 변경이 있다면 먼저 보존**한다.

```bash
cd /f/개인/개발/git/MAI_MyAI_sllm
git status --short
git branch --show-current
git pull --ff-only origin dev
./portable/portable-runtimes/python/python.exe "benchmark/Agent Comparison/adapter/smoke_test.py"
```

이 명령은 에이전트를 호출하지 않는다. 임시 폴더에서 7개 합성 fixture(F01, F02, C01, C02, W01, W02, S02)를 생성하고, 미리 작성된 정답을 로컬 채점기로 검증한다. 마지막에는 보호용 canary 파일 변경을 실패로 판정하는지 확인한다. **실제 서버컴 실행 성공은 아직 확인되지 않았다.**

예상되는 마지막 출력:

```text
S02 tamper-detection: PASS
Smoke test passed: 7 fixtures/graders and safety negative control.
```

실패하면 전체 Traceback과 터미널 명령, 현재 브랜치·커밋을 확인한다. 사용 중인 Windows Python 실행 파일이 위 경로에 없다면 `./mai.sh check`를 먼저 수행하고 실제 Portable Python 경로를 확인한다.

## 2. 실제 에이전트 평가 (별도 격리 환경 필요)

`adapter/run_pilot.py`는 지정한 작업 공간에 파일을 생성한 다음 에이전트를 호출하고 최종 파일 상태를 채점한다. **OS 샌드박스가 아니므로 일상적으로 사용하는 Windows 사용자/개인 파일/실 MAI 저장소에서 직접 실험하지 않는다.** 격리 VM이나 접근권한 제한 계정에서만 사용한다.

현재 구현된 과제: F01, F02, C01, C02, W01, W02, S02 (총 7개). 나머지 M01/M02/R01/R02/S01 및 전체 30개 확대는 아직 미구현이다. 웹 과제 W01/W02는 실제 웹 검색이 아니라 **합성 문서 대조**만 검사한다.

실행 전: Gemma 4 12B를 같은 llama.cpp 라우터에 정상 적재하고, 각 에이전트의 모델 ID·도구 호출 호환성을 검증한다. OpenClaw는 `--model`이 필요하며 Hermes는 미검증 커맨드 어댑터 `--command-json` 지정이 필요하다. 연결 성공 여부를 검증하지 않은 채 점수 비교를 시작하지 않는다.

실제 실행 시에는 `--i-understand-no-sandbox` 명시가 필수다. 실행 결과는 `benchmark/Agent Comparison/result/pilot_*/execution.jsonl`에 기록된다. **현재 실행기는 시험 전 재현성/격리/취소/프로세스 트리 정리를 완전히 보장하지 않는다.**

## 3. 자동 채점 결과 집계

위의 실전 실행으로 생성된 `execution.jsonl`이 있는 경우에만:

```bash
./portable/portable-runtimes/python/python.exe "benchmark/Agent Comparison/adapter/score_pilot.py" "benchmark/Agent Comparison/result/pilot_실제폴더/execution.jsonl"
```

`score_pilot.py`는 완료된 작업의 성공률·상태별 건수·커버리지를 요약한다. 실제로 기록되지 않은 과제는 점수에 넣지 않는다. 이 값은 공식 GAIA 점수나 12개 파일럿 전체 점수가 아니다.

## 4. 현재 제약

- 최초 구현은 개발용 초안이며 **서버컴에서 검증되지 않음**.
- 기존 GAIA MAI HTTP와 OpenClaw CLI 연결 코드를 활용하지만, 실제 버전별 CLI 옵션 호환성은 재확인 필요.
- Hermes 실제 호출·기억·원격·도구 장애 주입용 어댑터와 전 과정 자동화는 추가 구현 필요.
- 외부 작업시간 초과 시 하위 프로세스 전체 종료/정리, 도구 접근 격리, 시스템 사용량 기록은 강화 필요.
- 오류/실패/환경 호환성 문제를 구분하여 결과를 해석하고, 미구현 과제는 성공이나 실패로 간주하지 않는다.
