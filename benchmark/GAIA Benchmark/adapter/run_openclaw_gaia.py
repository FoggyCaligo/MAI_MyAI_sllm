"""GAIA -> OpenClaw one-shot evaluation. No MAI server or extra model server.

Uses the same GAIA loader, answer contract and predictions schema as MAI.
Requires a separately installed OpenClaw CLI and an already-running llama.cpp router.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.request import urlopen

from run_gaia import (
    BENCH_ROOT, RESULT_ROOT, completed_ids, extract_answer,
    load_examples, model_prompt, resolve_attachment,
)

PROJECT_ROOT = BENCH_ROOT.parents[1]
CONFIG_PROVIDER = "mai-local"
MODEL_API_URL = "http://127.0.0.1:8080/v1/models"


def router_model_ids(url: str = MODEL_API_URL) -> set[str]:
    with urlopen(url, timeout=10) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("llama.cpp /v1/models response lacks data list")
    ids = set()
    for item in payload["data"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("llama.cpp /v1/models contains invalid model id")
        ids.add(item["id"])
    if not ids:
        raise ValueError("llama.cpp router exposes no models")
    return ids


def default_mai_model() -> str:
    resolver = BENCH_ROOT / "adapter" / "resolve_main_model.py"
    process = subprocess.run(
        [sys.executable, str(resolver), "--root", str(PROJECT_ROOT)],
        text=True, capture_output=True, check=True, encoding="utf-8",
    )
    model = process.stdout.strip()
    if not model or len(model.splitlines()) != 1:
        raise ValueError("MAI GGUF resolver must return exactly one model ID")
    return model


def openclaw_config(model_id: str, *, context_size: int = 131072) -> dict:
    if not model_id or "/" in model_id:
        raise ValueError("invalid llama.cpp model alias")
    return {
        "models": {
            "mode": "replace",
            "providers": {
                CONFIG_PROVIDER: {
                    "baseUrl": "http://127.0.0.1:8080/v1",
                    "apiKey": "local-unauthed-llamacpp",
                    "api": "openai-completions",
                    "timeoutSeconds": 300,
                    "models": [{
                        "id": model_id, "name": model_id, "reasoning": False,
                        "input": ["text"],
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        "contextWindow": context_size, "maxTokens": 32768,
                    }],
                },
            },
        },
        "agents": {
            "defaults": {
                "model": {"primary": f"{CONFIG_PROVIDER}/{model_id}"},
                "contextTokens": context_size,
                "contextInjection": "never",
            },
        },
    }


def require_openclaw_version(executable: str) -> str:
    process = subprocess.run(
        [executable, "--version"], capture_output=True, text=True,
        timeout=30, encoding="utf-8",
    )
    if process.returncode != 0:
        raise RuntimeError(f"OpenClaw CLI version failed: {process.stderr.strip()}")
    version = process.stdout.strip()
    if not version:
        raise RuntimeError("OpenClaw CLI version returned an empty response")
    return version


def parse_envelope(stdout: str) -> dict:
    data = json.loads(stdout)
    if not isinstance(data, dict):
        raise TypeError("OpenClaw JSON output must be an object")
    if data.get("ok") is not True or data.get("status") != "ok":
        raise RuntimeError(f"OpenClaw agent exec failed: {data.get('error')!r}; status={data.get('status')!r}")
    final = data.get("final")
    if not isinstance(final, str):
        raise ValueError("OpenClaw JSON output lacks final string")
    return data


def run_one(
    *, executable: str, prompt: str, workspace: Path,
    config: Path, model_id: str, timeout_seconds: int,
) -> tuple[str, dict]:
    if not workspace.is_dir():
        raise FileNotFoundError(workspace)
    # Pass prompt by file rather than a command-line argument.
    prompt_file = workspace / "gaia_prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    cmd = [
        executable, "agent", "exec", "--message-file", str(prompt_file),
        "--cwd", str(workspace), "--config", str(config),
        "--model", f"{CONFIG_PROVIDER}/{model_id}",
        "--code-mode", "direct", "--timeout", str(timeout_seconds),
        "--json",
    ]
    try:
        process = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="strict", timeout=timeout_seconds + 90,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"OpenClaw subprocess exceeded {timeout_seconds + 90}s") from exc
    (workspace / "openclaw_stdout.json").write_text(process.stdout, encoding="utf-8")
    (workspace / "openclaw_stderr.log").write_text(process.stderr, encoding="utf-8")
    if process.returncode != 0:
        raise RuntimeError(
            f"OpenClaw agent exec exited {process.returncode}; "
            f"inspect {workspace / 'openclaw_stderr.log'} and openclaw_stdout.json"
        )
    envelope = parse_envelope(process.stdout)
    return extract_answer(envelope["final"]), envelope


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenClaw GAIA comparison to MAI")
    parser.add_argument("--split", choices=("validation", "test"), default="validation")
    parser.add_argument("--level", choices=("all", "level1", "level2", "level3"), default="level1")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--openclaw", default="openclaw", help="OpenClaw executable path")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.start < 0 or args.limit < 0 or args.timeout_seconds <= 0:
        parser.error("start >= 0, limit >= 0, timeout-seconds > 0 required")
    model_id = default_mai_model()
    if model_id not in router_model_ids():
        raise RuntimeError(f"MAI model alias {model_id!r} not exposed by llama.cpp router 8080")
    version = require_openclaw_version(args.openclaw)
    dataset = load_examples(args.split, args.level)
    if args.start >= len(dataset):
        raise ValueError("start exceeds available dataset")
    end = len(dataset) if args.limit == 0 else min(len(dataset), args.start + args.limit)
    output = args.output_dir or RESULT_ROOT / datetime.now(timezone.utc).strftime("openclaw_%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=True)
    config = output / "openclaw.gaia.json"
    manifest_file = output / "manifest.json"
    settings = {
        "agent": "openclaw", "split": args.split, "level": args.level,
        "start": args.start, "end": end, "model_id": model_id,
        "openclaw_version": version, "timeout_seconds": args.timeout_seconds,
        "endpoint": "http://127.0.0.1:8080/v1", "code_mode": "direct",
    }
    if manifest_file.exists():
        if json.loads(manifest_file.read_text(encoding="utf-8")) != settings:
            raise ValueError("existing OpenClaw run has different settings")
        if json.loads(config.read_text(encoding="utf-8")) != openclaw_config(model_id):
            raise ValueError("existing OpenClaw model configuration differs")
    else:
        manifest_file.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
        config.write_text(json.dumps(openclaw_config(model_id), indent=2, ensure_ascii=False), encoding="utf-8")
    predictions = output / "predictions.jsonl"
    executions = output / "execution.jsonl"
    done = completed_ids(predictions)
    for index in range(args.start, end):
        task = dict(dataset[index])
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError(f"GAIA task_id invalid at index {index}")
        if task_id in done:
            print(f"[{index - args.start + 1}/{end - args.start}] skipped {task_id}", flush=True)
            continue
        started = time.monotonic()
        answer = ""
        status = "failed"
        error = None
        metrics = {}
        try:
            attachment = resolve_attachment(task.get("file_path"))
            workdir = output / "workspaces" / task_id
            workdir.mkdir(parents=True, exist_ok=False)
            staged = None
            if attachment is not None:
                staged = workdir / attachment.name
                shutil.copy2(attachment, staged)
            # Keep the attachment path accessible through the workspace tool boundary.
            prompt = model_prompt(task, attachment_path=staged)
            answer, envelope = run_one(
                executable=args.openclaw, prompt=prompt,
                workspace=workdir, config=config,
                model_id=model_id, timeout_seconds=args.timeout_seconds,
            )
            metrics = {
                "assistant_turns": envelope.get("assistantTurns"),
                "tool_summary": envelope.get("toolSummary"),
                "usage": envelope.get("usage"),
                "provider": envelope.get("provider"),
                "model": envelope.get("model"),
            }
            status = "completed"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        elapsed = round(time.monotonic() - started, 3)
        with predictions.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"task_id": task_id, "model_answer": answer}, ensure_ascii=False) + "\n")
        with executions.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "task_id": task_id, "status": status, "error": error,
                "elapsed_seconds": elapsed, "metrics": metrics,
            }, ensure_ascii=False) + "\n")
        done.add(task_id)
        print(f"[{index - args.start + 1}/{end - args.start}] {task_id} {status} {elapsed}s", flush=True)
        if error:
            print(f"  {error}", flush=True)


if __name__ == "__main__":
    main()
