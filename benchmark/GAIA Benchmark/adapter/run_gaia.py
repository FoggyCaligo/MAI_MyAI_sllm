"""GAIA -> MAI 실제 HTTP 작업 실행 및 JSONL 체크포인트.

외부 도구에는 GAIA 정답/채점 파일을 전달하지 않는다.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
import shutil
from pathlib import Path
import time
from uuid import uuid4

import httpx

from download_gaia import DATA_DIR

BENCH_ROOT = Path(__file__).resolve().parents[1]
RESULT_ROOT = BENCH_ROOT / "result"
TERMINAL = {"completed", "failed", "cancelled"}
ANSWER_PREFIX = "FINAL ANSWER:"


def resolve_attachment(file_path: object) -> Path | None:
    if file_path is None or file_path == "":
        return None
    if not isinstance(file_path, str):
        raise ValueError("GAIA file_path must be a relative string")
    candidate = (DATA_DIR / file_path).resolve(strict=True)
    if not candidate.is_relative_to(DATA_DIR.resolve(strict=True)):
        raise ValueError("GAIA attachment escapes the local dataset")
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def model_prompt(example: dict[str, object], *, attachment_path: Path | None = None) -> str:
    question = example.get("Question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("GAIA Question must be a non-empty string")
    prompt = question
    attachment = attachment_path if attachment_path is not None else resolve_attachment(example.get("file_path"))
    if attachment is not None:
        # The benchmark runner and MAI server MUST run on the same host.
        # Non-image attachments must be accessed through existing MAI file tools.
        prompt += f"\n\nAttached GAIA file (absolute local path): {attachment}"
    prompt += (
        "\n\nGive the answer in the shortest possible form. "
        "The LAST line of your reply MUST be exactly: FINAL ANSWER: <answer>. "
        "For a number omit units and digit grouping unless the question explicitly requires them. "
        "Do not add text after this line."
    )
    return prompt


def extract_answer(response: str) -> str:
    # An explicit output contract, not an attempt to guess answers from prose.
    if not isinstance(response, str):
        raise TypeError("MAI answer must be a string")
    last_line = response.rstrip().splitlines()[-1] if response.strip() else ""
    if not last_line.startswith(ANSWER_PREFIX):
        raise ValueError("MAI final answer did not follow GAIA answer format")
    answer = last_line[len(ANSWER_PREFIX):].strip()
    if not answer:
        raise ValueError("GAIA final answer is empty")
    return answer


def login(client: httpx.Client, user: str, password: str) -> dict[str, str]:
    preauth = client.get("/login/session")
    preauth.raise_for_status()
    session = preauth.json()
    if not isinstance(session, dict) or not isinstance(session.get("session_token"), str) or not session["session_token"]:
        raise RuntimeError("MAI pre-login response has no session_token")
    response = client.post(
        "/login",
        headers={"X-MAI-Preauth-Session": session["session_token"]},
        json={"user_id": user, "user_pw": password},
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("token"), str):
        raise RuntimeError("MAI login response has no token")
    return {"Authorization": f"Bearer {data['token']}"}


def submit_and_poll(
    client: httpx.Client, headers: dict[str, str], payload: dict[str, object],
    *, deadline_seconds: int, poll_seconds: float,
) -> dict[str, object]:
    response = client.post("/chat/jobs", headers=headers, json=payload)
    response.raise_for_status()
    job = response.json()
    if not isinstance(job, dict) or not isinstance(job.get("job_id"), str):
        raise RuntimeError("MAI jobs endpoint did not return job_id")
    job_id = job["job_id"]
    deadline = time.monotonic() + deadline_seconds
    while True:
        response = client.get(f"/chat/jobs/{job_id}", headers=headers)
        response.raise_for_status()
        state = response.json()
        if not isinstance(state, dict) or state.get("status") not in TERMINAL | {"pending", "running", "cancelling"}:
            raise RuntimeError("MAI jobs endpoint returned invalid status")
        if state["status"] in TERMINAL:
            return state
        if time.monotonic() >= deadline:
            cancel = client.delete(f"/chat/jobs/{job_id}", headers=headers)
            cancel.raise_for_status()
            raise TimeoutError(f"MAI GAIA task exceeded {deadline_seconds} seconds; job {job_id} cancelled")
        time.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))


def load_examples(split: str, level: str):
    from datasets import load_dataset
    if not DATA_DIR.is_dir():
        raise FileNotFoundError(f"GAIA files missing; run setup first: {DATA_DIR}")
    config = "2023_all" if level == "all" else f"2023_{level}"
    return load_dataset(str(DATA_DIR), config, split=split)


def completed_ids(path: Path) -> set[str]:
    completed: set[str] = set()
    if path.exists():
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                record = json.loads(line)
                task_id = record["task_id"]
                if not isinstance(task_id, str):
                    raise ValueError("invalid existing prediction task_id")
                if task_id in completed:
                    raise ValueError("duplicate task_id in prediction file")
                completed.add(task_id)
    return completed


def main() -> None:
    parser = argparse.ArgumentParser(description="격리된 GAIA -> MAI 실행")
    parser.add_argument("--split", choices=("validation", "test"), default="validation")
    parser.add_argument("--level", choices=("all", "level1", "level2", "level3"), default="level1")
    parser.add_argument("--limit", type=int, default=10, help="0은 선택한 split 전체")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--user", default=os.environ.get("MAI_BENCH_USER"))
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--output-dir", type=Path, help="기존 출력 경로면 완료 task_id를 재실행하지 않음")
    parser.add_argument("--model", help="MAI 모델 선택값, 생략 시 서버 기본 모델")
    parser.add_argument("--verify", action="store_true", help="Verifier 실험군에서만 설정")
    args = parser.parse_args()
    if not args.user:
        parser.error("--user 또는 MAI_BENCH_USER 필요")
    if args.limit < 0 or args.start < 0 or args.timeout_seconds <= 0 or args.poll_seconds <= 0:
        parser.error("limit/start/timeout/poll 값이 올바르지 않음")
    dataset = load_examples(args.split, args.level)
    if args.start >= len(dataset):
        parser.error("start가 선택한 데이터셋 범위를 벗어남")
    end = len(dataset) if args.limit == 0 else min(len(dataset), args.start + args.limit)
    output = args.output_dir or RESULT_ROOT / datetime.now(timezone.utc).strftime("gaia_%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=True)
    prediction_file = output / "predictions.jsonl"
    detail_file = output / "execution.jsonl"
    manifest_file = output / "manifest.json"
    run_settings = {
        "split": args.split, "level": args.level, "start": args.start, "end": end,
        "server": args.server, "model": args.model, "verify": args.verify,
        "timeout_seconds": args.timeout_seconds,
    }
    if manifest_file.exists():
        old = json.loads(manifest_file.read_text(encoding="utf-8"))
        if old != run_settings:
            raise ValueError("existing result directory has different benchmark settings")
    else:
        manifest_file.write_text(json.dumps(run_settings, indent=2), encoding="utf-8")
    done = completed_ids(prediction_file)
    password = os.environ.get("MAI_BENCH_PASSWORD") or getpass.getpass("MAI benchmark 전용 계정 암호: ")
    with httpx.Client(base_url=args.server.rstrip("/"), timeout=30.0) as client:
        headers = login(client, args.user, password)
        for index in range(args.start, end):
            example = dict(dataset[index])
            task_id = example.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                raise ValueError(f"invalid GAIA task_id at index {index}")
            if task_id in done:
                continue
            start_time = time.monotonic()
            try:
                attachment = resolve_attachment(example.get("file_path"))
                staged = None
                if attachment is not None:
                    # Do not reveal the answer-bearing dataset directory to MAI.
                    # Staging is local only; run MAI under a restricted OS account
                    # if its tools must not access dataset metadata at all.
                    staged_dir = output / "inputs" / task_id
                    staged_dir.mkdir(parents=True, exist_ok=True)
                    staged = staged_dir / attachment.name
                    shutil.copy2(attachment, staged)
                payload = {
                    "message": model_prompt(example, attachment_path=staged),
                    "session_id": "gaia-" + uuid4().hex,
                    "verify": args.verify,
                }
                if args.model:
                    payload["model"] = args.model
                state = submit_and_poll(
                    client, headers, payload, deadline_seconds=args.timeout_seconds,
                    poll_seconds=args.poll_seconds,
                )
                status = state["status"]
                response = state.get("response")
                if not isinstance(response, dict):
                    raise RuntimeError("MAI job has no structured response")
                answer_text = response.get("answer")
                if status != "completed":
                    raise RuntimeError(f"MAI job finished as {status}: {state.get('error')}")
                answer = extract_answer(answer_text)
                outcome = "completed"
                error = None
                rounds = response.get("model_rounds")
                tool_calls = len(response.get("tools", []))
            except Exception as exc:
                answer = ""
                outcome = "failed"
                error = f"{type(exc).__name__}: {exc}"
                rounds, tool_calls = None, None
            seconds = round(time.monotonic() - start_time, 3)
            prediction = {"task_id": task_id, "model_answer": answer}
            detail = {
                "task_id": task_id, "index": index, "status": outcome, "error": error,
                "elapsed_seconds": seconds, "model_rounds": rounds, "tool_calls": tool_calls,
            }
            # Every task checkpoint is flushed to disk before continuing.
            with prediction_file.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(prediction, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            with detail_file.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(detail, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            done.add(task_id)
            print(f"[{index + 1}/{end}] {task_id} {outcome} {seconds:.1f}s", flush=True)
            if error:
                print(f"  {error}", flush=True)
    print(f"결과 저장: {output}")


if __name__ == "__main__":
    main()
