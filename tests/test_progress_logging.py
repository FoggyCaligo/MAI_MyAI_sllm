from __future__ import annotations

from dataclasses import dataclass, field

from mai import progress


@dataclass
class Recorder:
    infos: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)
    exceptions: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)

    def info(self, message: str, *args: object) -> None:
        self.infos.append((message, args))

    def exception(self, message: str, *args: object) -> None:
        self.exceptions.append((message, args))


def _render(record: tuple[str, tuple[object, ...]]) -> str:
    message, args = record
    return message % args if args else message


def test_progress_logs_phase_round_action_and_tool_without_payload(monkeypatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(progress, "_logger", recorder)

    progress.turn_started("turn-1")
    with progress.phase("turn-1", "work"):
        first_round = progress.model_request_started()
        progress.model_request_completed(first_round)
        progress.model_action("tool", "file_read")
        progress.tool_started("file_read")
        progress.tool_completed("file_read")
        second_round = progress.model_request_started()
        progress.model_request_completed(second_round)
        progress.model_action("answer")
    progress.turn_completed("turn-1")

    lines = [_render(record) for record in recorder.infos]
    assert "[Mai][turn=turn-1] turn started" in lines
    assert "[Mai][turn=turn-1][work] phase started" in lines
    assert "[Mai][turn=turn-1][work][round=1] model request started" in lines
    assert "[Mai][turn=turn-1][work] model action=tool tool=file_read" in lines
    assert "[Mai][turn=turn-1][work] tool=file_read started" in lines
    assert "[Mai][turn=turn-1][work] tool=file_read completed" in lines
    assert "[Mai][turn=turn-1][work][round=2] model request started" in lines
    assert "[Mai][turn=turn-1][work] model action=answer" in lines
    assert "[Mai][turn=turn-1][work] phase completed" in lines
    assert "[Mai][turn=turn-1] turn completed" in lines
    assert recorder.exceptions == []


def test_phase_failure_is_logged_and_reraised(monkeypatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(progress, "_logger", recorder)

    try:
        with progress.phase("turn-2", "memory_discovery"):
            raise RuntimeError("boom")
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("phase exception must propagate")

    lines = [_render(record) for record in recorder.exceptions]
    assert "[Mai][turn=turn-2][memory_discovery] phase failed" in lines
