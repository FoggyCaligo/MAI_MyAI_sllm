"""Strict LongMemEval-S replay for MAI's *existing* chat and memory lifecycle.

Each source user+assistant turn is admitted separately with original text. The
original assistant turn is not regenerated. This is an offline, source-grounded
memory test rather than an interactive reproduction of historic tool actions.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[2]
GAIA_ADAPTER = PROJECT_ROOT / "benchmark" / "GAIA Benchmark" / "adapter"
sys.path.insert(0, str(GAIA_ADAPTER))
sys.path.insert(0, str(PROJECT_ROOT))
from resolve_main_model import resolve_main_model
from dataset import dialogue_pairs, load_cases
from mai.app.access import AccessPrincipal, AccessRole
from mai.app.chat_sessions import ChatSessionStore
from mai.app.runtime import MAIRuntime


async def evaluate_case(*, case, root: Path, model: str, router: str) -> dict:
    case_dir = root / hashlib.sha256(case.question_id.encode("utf-8")).hexdigest()
    if case_dir.exists():
        raise FileExistsError(f"case workspace exists: {case_dir}")
    case_dir.mkdir(parents=True)
    principal = AccessPrincipal(
        user_id="longmemeval",
        db_id="longmemeval-" + hashlib.sha256(case.question_id.encode("utf-8")).hexdigest()[:32],
        role=AccessRole.OWNER,
    )
    runtime = MAIRuntime(
        model=model, llamacpp_host=router,
        memory_db_path=case_dir / "memory.sqlite3",
        sentence_breaker_db_path=case_dir / "sentence_breaker.sqlite3",
        upload_root=case_dir / "uploads", cwd=case_dir,
    )
    chat = ChatSessionStore(case_dir / "chat.sqlite3")
    observed_turns = 0
    started = time.monotonic()
    try:
        previous_assistant = None
        for session in case.sessions:
            runtime.memory.now = lambda when=session.date: when
            for user_text, original_assistant in dialogue_pairs(session):
                chat.append(
                    db_id=principal.db_id, session_id=session.session_id,
                    role="user", content=user_text,
                    created_at=session.date.timestamp(),
                )
                chat.append(
                    db_id=principal.db_id, session_id=session.session_id,
                    role="assistant", content=original_assistant,
                    created_at=session.date.timestamp(),
                )
                # Reuse the production fact extractor and durable memory admission.
                # Unlike MAI's background path, errors must stop this benchmark.
                facts = await runtime.memory.extract_facts(
                    user_text=user_text,
                    previous_assistant_message=previous_assistant,
                    final_answer=original_assistant,
                    successful_tool_results=(),
                    fact_extractor=runtime._fact_extractor_for(model),
                )
                evidence = runtime.memory.record_raw_user_evidence(
                    principal.memory_user_id, user_text,
                )
                await runtime.memory.finish_turn(
                    user_id=principal.memory_user_id,
                    user_text=user_text,
                    previous_assistant_message=previous_assistant,
                    final_answer=original_assistant,
                    user_evidence=evidence,
                    successful_tool_results=(),
                    fact_texts=facts,
                )
                previous_assistant = original_assistant
                observed_turns += 1

        # Separate query session, no original history in model context.
        runtime.memory.now = lambda: datetime.now(timezone.utc)
        answer_result = await runtime.run_user_message(
            case.question, principal=principal, prior_messages=(), verify=False,
        )
        await runtime._await_pending_memory_update(principal.memory_user_id)
        answer = answer_result.answer
        status = "completed"
        error = None
    except Exception as exc:
        answer = ""
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
    finally:
        runtime.close()
    return {
        "question_id": case.question_id,
        "question_type": case.question_type,
        "status": status,
        "model_answer": answer,
        "error": error,
        "history_sessions": len(case.sessions),
        "history_turn_pairs": observed_turns,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="MAI LongMemEval original conversation replay")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--router", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("A fresh output directory is required; existing memory must not be reused")
    model = resolve_main_model(PROJECT_ROOT)
    args.output_dir.mkdir(parents=True)
    (args.output_dir / "manifest.json").write_text(json.dumps({
        "benchmark": "LongMemEval", "agent": "MAI", "model": model,
        "dataset": str(args.dataset.resolve()), "start": args.start,
        "limit": args.limit, "router": args.router,
        "replay_mode": "original_assistant_text_per_completed_turn",
        "tool_logs": "not supplied by LongMemEval",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    results = args.output_dir / "predictions.jsonl"
    for index, case in enumerate(load_cases(args.dataset, start=args.start, limit=args.limit), 1):
        result = asyncio.run(evaluate_case(
            case=case, root=args.output_dir / "cases", model=model, router=args.router,
        ))
        with results.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
        print(
            f"[{index}] {case.question_id}: {result['status']}; "
            f"{result['history_turn_pairs']} replayed turns; "
            f"{result['elapsed_seconds']}s", flush=True,
        )
        if result["error"]:
            raise RuntimeError(f"LongMemEval case failed: {result['error']}")


if __name__ == "__main__":
    main()
