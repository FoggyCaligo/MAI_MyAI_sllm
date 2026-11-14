"""LongMemEval-S -> OpenClaw native transcript import and fresh-session recall.

Uses the OpenClaw plugin SDK, never writes OpenClaw SQLite tables or fabricated
Markdown memory directly. The data passed to the native plugin excludes gold.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
GAIA_ADAPTER = ROOT / "benchmark" / "GAIA Benchmark" / "adapter"
sys.path.insert(0, str(GAIA_ADAPTER))
from run_openclaw_gaia import (
    CONFIG_PROVIDER, MODEL_TIMEOUT_SECONDS, default_mai_model, openclaw_config, parse_envelope,
    require_openclaw_version, resolve_openclaw_executable, router_model_ids,
)
from dataset import load_cases

PLUGIN_DIR = HERE.parent / "openclaw-plugin"


def safe_filename(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def sanitized_history(case) -> dict:
    sessions = []
    for session in case.sessions:
        sessions.append({
            "session_id": session.session_id,
            "timestamp_ms": int(session.date.timestamp() * 1000),
            "turns": [
                {"role": turn.role, "content": turn.content}
                for turn in session.turns
            ],
        })
    return {"case_id": case.question_id, "sessions": sessions}


def benchmark_config(model: str, plugin_path: Path) -> dict:
    if not plugin_path.is_dir():
        raise NotADirectoryError(plugin_path)
    if not (plugin_path / "openclaw.plugin.json").is_file():
        raise FileNotFoundError(plugin_path / "openclaw.plugin.json")
    config = openclaw_config(model)
    config["memory"] = {
        "search": {
            "enabled": True,
            "provider": "none",
            "fallback": "none",
            "sources": ["memory", "sessions"],
            "experimental": {"sessionMemory": True},
            "rememberAcrossConversations": True,
        },
    }
    config["tools"] = {"sessions": {"visibility": "agent"}}
    config["plugins"] = {
        "load": {"paths": [str(plugin_path.resolve())]},
        "allow": ["longmemeval-replay", "memory-core"],
        "entries": {
            "longmemeval-replay": {"enabled": True},
            "memory-core": {"enabled": True},
        },
    }
    return config


def run_command(cmd: list[str], *, env: dict[str, str], timeout: int, log_dir: Path, label: str) -> str:
    started = time.monotonic()
    result = subprocess.run(
        cmd, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="strict", timeout=timeout,
    )
    (log_dir / f"{label}.stdout").write_text(result.stdout, encoding="utf-8")
    (log_dir / f"{label}.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"{label} exited {result.returncode}; inspect {log_dir / (label + '.stderr')}")
    return result.stdout


def verify_import(stdout: str, case) -> None:
    data = json.loads(stdout)
    expected_sessions = len(case.sessions)
    expected_messages = sum(len(session.turns) for session in case.sessions)
    if (
        not isinstance(data, dict)
        or data.get("ok") is not True
        or data.get("mode") != "native_transcript_replay"
        or data.get("case_id") != case.question_id
        or data.get("sessions") != expected_sessions
        or data.get("messages") != expected_messages
    ):
        raise ValueError("OpenClaw native transcript import confirmation disagrees with input")


def run_case(*, case, output: Path, config: Path, cli: str, model: str, timeout: int) -> dict:
    folder = output / "cases" / safe_filename(case.question_id)
    folder.mkdir(parents=True, exist_ok=False)
    state = folder / "state"
    state.mkdir()
    workspace = folder / "workspace"
    workspace.mkdir()
    payload_file = folder / "history.json"
    payload_file.write_text(json.dumps(sanitized_history(case), ensure_ascii=False), encoding="utf-8")
    question_file = folder / "question.txt"
    question_file.write_text(case.question, encoding="utf-8")
    env = dict(os.environ)
    env["OPENCLAW_STATE_DIR"] = str(state.resolve())
    env["OPENCLAW_CONFIG_PATH"] = str(config.resolve())
    count = sum(len(session.turns) for session in case.sessions)
    started = time.monotonic()
    record = {
        "question_id": case.question_id,
        "question_type": case.question_type,
        "agent": "OpenClaw",
        "memory_protocol": "native_transcript_replay_session_memory_fts",
        "history_sessions": len(case.sessions),
        "history_messages": count,
        "model_answer": "", "status": "failed", "error": None,
    }
    try:
        imported = run_command(
            [cli, "longmemeval", "import", "--input", str(payload_file.resolve())],
            env=env, timeout=max(1800, timeout), log_dir=folder, label="import",
        )
        verify_import(imported.strip(), case)
        run_command(
            [cli, "memory", "index", "--force", "--agent", "main"],
            env=env, timeout=max(1800, timeout), log_dir=folder, label="index",
        )
        output_json = run_command(
            [cli, "agent", "exec", "--message-file", str(question_file.resolve()),
             "--cwd", str(workspace.resolve()), "--state-dir", str(state.resolve()),
             "--config", str(config.resolve()),
             "--model", f"{CONFIG_PROVIDER}/{model}", "--code-mode", "direct",
             "--timeout", str(timeout), "--json"],
            env=env, timeout=timeout + 90, log_dir=folder, label="answer",
        )
        envelope = parse_envelope(output_json)
        final = envelope["final"]
        if not isinstance(final, str) or not final.strip():
            raise ValueError("OpenClaw final answer is empty")
        record["model_answer"] = final
        record["status"] = "completed"
        record["metrics"] = {
            "assistant_turns": envelope.get("assistantTurns"),
            "tool_summary": envelope.get("toolSummary"),
            "usage": envelope.get("usage"),
        }
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenClaw native LongMemEval-S replay")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--openclaw", default="openclaw")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args()
    if args.start < 0 or args.limit < 0 or args.timeout_seconds <= 0:
        parser.error("invalid start/limit/timeout")
    if args.output_dir.exists():
        raise FileExistsError("LongMemEval requires a fresh result directory")
    cases = tuple(load_cases(args.dataset, start=args.start, limit=args.limit))
    if not cases:
        raise ValueError("selected LongMemEval dataset slice is empty")
    model = default_mai_model()
    if model not in router_model_ids():
        raise RuntimeError(f"Selected MAI GGUF {model!r} not in router catalog")
    executable = resolve_openclaw_executable(args.openclaw)
    version = require_openclaw_version(executable)
    args.output_dir.mkdir(parents=True)
    config = args.output_dir / "openclaw.json"
    config.write_text(
        json.dumps(benchmark_config(model, PLUGIN_DIR), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Validate the generated config with the installed OpenClaw before importing.
    # A valid schema can still carry warnings about inaccessible plugin paths.
    validation_env = dict(os.environ)
    validation_env["OPENCLAW_CONFIG_PATH"] = str(config.resolve())
    validation_stdout = run_command(
        [executable, "config", "validate", "--json"],
        env=validation_env, timeout=60, log_dir=args.output_dir, label="config_validation",
    )
    validation = json.loads(validation_stdout)
    if not isinstance(validation, dict) or validation.get("valid") is not True:
        raise ValueError(f"OpenClaw configuration validation failed: {validation!r}")
    warnings = validation.get("warnings")
    if not isinstance(warnings, list) or warnings:
        raise ValueError(f"OpenClaw configuration validation warnings: {warnings!r}")
    (args.output_dir / "manifest.json").write_text(json.dumps({
        "agent": "OpenClaw", "benchmark": "LongMemEval-S",
        "version": version, "model": model,
        "dataset": str(args.dataset.resolve()), "start": args.start, "limit": args.limit,
        "memory_protocol": "native_transcript_replay_session_memory_fts",
        "context": 131072, "max_tokens": 32768,
        "provider_timeout_seconds": MODEL_TIMEOUT_SECONDS, "task_timeout_seconds": args.timeout_seconds,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    prediction_file = args.output_dir / "predictions.jsonl"
    for index, case in enumerate(cases, 1):
        result = run_case(
            case=case, output=args.output_dir, config=config,
            cli=executable, model=model, timeout=args.timeout_seconds,
        )
        with prediction_file.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
        print(
            f"[{index}/{len(cases)}] {case.question_id}: {result['status']} "
            f"({result['history_messages']} historical messages, {result['elapsed_seconds']}s)",
            flush=True,
        )
        if result["error"]:
            raise RuntimeError(result["error"])


if __name__ == "__main__":
    main()
