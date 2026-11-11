"""Regression contract for cancelling detached MAI jobs without limiting model rounds."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_browser_abort_requests_server_job_cancellation():
    source = (ROOT / "mai/app/static/resumable-chat.js").read_text(encoding="utf-8")
    assert "const signal = init?.signal;" in source
    assert "signal?.addEventListener('abort', cancelOnAbort, {once: true});" in source
    assert "if (signal?.aborted) cancelOnAbort();" in source
    assert "if (!cancellation) cancellation = cancelJob(jobId);" in source
    assert "const submit = await nativeFetch('/chat/jobs', {...init, signal: undefined});" in source
    assert "method: 'DELETE'" in source


def test_main_prompt_has_no_forced_brevity_and_no_round_cap():
    prompt = (ROOT / "mai/app/runtime.py").read_text(encoding="utf-8")
    loop = (ROOT / "mai/agent/loop.py").read_text(encoding="utf-8")
    settings = (ROOT / "settings.yaml").read_text(encoding="utf-8")
    assert "Keep final answers concise and focused" not in prompt
    assert "AGENT_MAX_MODEL_ROUNDS" not in loop
    assert "AGENT_MAX_MODEL_ROUNDS" not in settings
