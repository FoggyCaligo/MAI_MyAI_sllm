"""Move legacy MAI data safely, preserving owner files and MCP toggles."""
from pathlib import Path

import pytest

from portable.runtime_data_paths import (
    migrate, normalize_legacy_environment, DataPathMigrationError,
    MEMORY_RELATIVE, LEGACY_MEMORY_RELATIVE, UPLOAD_RELATIVE,
)

def _root(tmp_path):
    (tmp_path / "mai").mkdir()
    return tmp_path

def test_moves_old_db_and_uploads_preserving_bytes(tmp_path):
    root = _root(tmp_path)
    old_mem = root / LEGACY_MEMORY_RELATIVE
    old_mem.mkdir(parents=True)
    (old_mem / "memory.sqlite3").write_bytes(b"existing-memory-db")
    (old_mem / "sentence_breaker.sqlite3").write_bytes(b"existing-segmenter")
    (old_mem / "chat.sqlite3").write_bytes(b"existing-chat")
    (old_mem / "log.sqlite3").write_bytes(b"existing-log")
    upload = root / "mai_uploads" / "user123"
    upload.mkdir(parents=True)
    (upload / "photo.png").write_bytes(b"private-user-file")

    status = migrate(root)
    assert status["memory"] and status["uploads"]
    assert (root / MEMORY_RELATIVE / "memory.sqlite3").read_bytes() == b"existing-memory-db"
    assert (root / MEMORY_RELATIVE / "chat.sqlite3").read_bytes() == b"existing-chat"
    assert (root / UPLOAD_RELATIVE / "user123/photo.png").read_bytes() == b"private-user-file"
    assert not (root / LEGACY_MEMORY_RELATIVE).exists()
    assert not (root / "mai_uploads").exists()
    assert not migrate(root)["memory"]

def test_refuses_collision_without_overwriting(tmp_path):
    root = _root(tmp_path)
    old = root / LEGACY_MEMORY_RELATIVE
    old.mkdir(parents=True)
    (old / "memory.sqlite3").write_bytes(b"old")
    destination = root / MEMORY_RELATIVE
    destination.mkdir(parents=True)
    (destination / "memory.sqlite3").write_bytes(b"new")
    with pytest.raises(DataPathMigrationError, match="Refusing to overwrite or merge"):
        migrate(root)
    assert (old / "memory.sqlite3").read_bytes() == b"old"
    assert (destination / "memory.sqlite3").read_bytes() == b"new"

def test_unused_legacy_mcp_configuration_does_not_block_native_migration(tmp_path):
    root = _root(tmp_path)
    config = root / "mai/mcp_tools/mcp_servers.json"
    config.parent.mkdir(parents=True)
    config.write_text("{invalid old MCP configuration", encoding="utf-8")
    old_data = root / LEGACY_MEMORY_RELATIVE
    old_data.mkdir(parents=True)
    (old_data / "memory.sqlite3").write_bytes(b"old DB")
    status = migrate(root)
    assert status["memory"] is True
    assert (root / MEMORY_RELATIVE / "memory.sqlite3").read_bytes() == b"old DB"
    assert config.read_text(encoding="utf-8") == "{invalid old MCP configuration"


def test_redirects_only_exact_old_default_env_paths(tmp_path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setenv("MEMORY_DB_PATH", str(root / LEGACY_MEMORY_RELATIVE / "memory.sqlite3"))
    monkeypatch.setenv("MAI_UPLOAD_ROOT", str(root / "mai_uploads"))
    monkeypatch.setenv("CHAT_DB_PATH", str(root / "elsewhere/chat.sqlite3"))
    new_values = normalize_legacy_environment(root)
    assert new_values["MEMORY_DB_PATH"] == str(root / MEMORY_RELATIVE / "memory.sqlite3")
    assert new_values["MAI_UPLOAD_ROOT"] == str(root / UPLOAD_RELATIVE)
    assert "CHAT_DB_PATH" not in new_values
    assert (root / "mai").is_dir()


def test_legacy_mcp_config_inside_memory_dir_is_moved_but_not_rewritten(tmp_path):
    root = _root(tmp_path)
    old_data = root / LEGACY_MEMORY_RELATIVE
    old_data.mkdir(parents=True)
    (old_data / "memory.sqlite3").write_bytes(b"old-memory")
    legacy_config = old_data / "mcp_servers.json"
    legacy_config.write_text("{invalid legacy MCP metadata", encoding="utf-8")
    outcome = migrate(root)
    assert outcome["memory"] is True
    migrated = root / MEMORY_RELATIVE / "mcp_servers.json"
    assert migrated.read_text(encoding="utf-8") == "{invalid legacy MCP metadata"
    assert (root / MEMORY_RELATIVE / "memory.sqlite3").read_bytes() == b"old-memory"
