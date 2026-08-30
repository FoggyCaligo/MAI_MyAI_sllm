"""Turn-local temporary artifact tracking for model-created scratch files."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


class TemporaryArtifactCleanupError(RuntimeError):
    """A registered temporary artifact could not be removed after a successful run."""


@dataclass(slots=True)
class TemporaryArtifactLedger:
    paths: set[Path] = field(default_factory=set)

    def register(self, path: str | Path) -> None:
        self.paths.add(Path(path).expanduser().resolve(strict=False))

    def move(self, source: str | Path, destination: str | Path) -> None:
        src = Path(source).expanduser().resolve(strict=False)
        if src not in self.paths:
            return
        self.paths.remove(src)
        self.paths.add(Path(destination).expanduser().resolve(strict=False))

    def discard(self, path: str | Path) -> None:
        self.paths.discard(Path(path).expanduser().resolve(strict=False))

    def cleanup(self) -> None:
        for path in sorted(self.paths, key=lambda item: len(item.parts), reverse=True):
            if not path.exists():
                continue
            if not path.is_file() and not path.is_symlink():
                raise TemporaryArtifactCleanupError(
                    f"registered temporary artifact is no longer a file: {path}"
                )
            try:
                path.unlink()
            except OSError as exc:
                raise TemporaryArtifactCleanupError(
                    f"failed to remove temporary artifact: {path}"
                ) from exc
        self.paths.clear()


_CURRENT_LEDGER: ContextVar[TemporaryArtifactLedger | None] = ContextVar(
    "mai_temporary_artifact_ledger",
    default=None,
)


@contextmanager
def temporary_artifact_scope() -> Iterator[TemporaryArtifactLedger]:
    ledger = TemporaryArtifactLedger()
    token = _CURRENT_LEDGER.set(ledger)
    try:
        yield ledger
    finally:
        _CURRENT_LEDGER.reset(token)


def register_temporary_artifact(path: str | Path) -> None:
    ledger = _CURRENT_LEDGER.get()
    if ledger is not None:
        ledger.register(path)


def move_temporary_artifact(source: str | Path, destination: str | Path) -> None:
    ledger = _CURRENT_LEDGER.get()
    if ledger is not None:
        ledger.move(source, destination)


def discard_temporary_artifact(path: str | Path) -> None:
    ledger = _CURRENT_LEDGER.get()
    if ledger is not None:
        ledger.discard(path)
