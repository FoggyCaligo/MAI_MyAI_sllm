from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_local_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _path_env(name: str, default: Path | None = None) -> Path | None:
    raw = os.getenv(name, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return default.resolve() if default is not None else None


def _csv_env(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    return tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))


_load_local_env(ROOT_DIR / ".env")


@dataclass(frozen=True, slots=True)
class Settings:
    ollama_host: str
    ollama_model: str
    server_host: str
    server_port: int
    db_path: Path
    sentence_breaker_db_path: Path | None
    require_sentence_breaker: bool
    allowed_login_ids: tuple[str, ...]


def load_settings() -> Settings:
    data_dir = ROOT_DIR / "data"
    return Settings(
        ollama_host=os.getenv("MAI_OLLAMA_HOST", "http://127.0.0.1:11434").strip(),
        ollama_model=os.getenv("MAI_OLLAMA_MODEL", "qwen3.5:9b").strip(),
        server_host=os.getenv("MAI_SERVER_HOST", "127.0.0.1").strip(),
        server_port=int(os.getenv("MAI_SERVER_PORT", "8010")),
        db_path=_path_env("MAI_DB_PATH", data_dir / "memory.db") or (data_dir / "memory.db").resolve(),
        sentence_breaker_db_path=_path_env("MAI_SENTENCE_BREAKER_DB_PATH"),
        require_sentence_breaker=_bool_env("MAI_REQUIRE_SENTENCE_BREAKER", True),
        allowed_login_ids=_csv_env("MAI_ALLOWED_LOGIN_IDS"),
    )


settings = load_settings()
