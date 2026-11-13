"""Move legacy MAI data safely, preserving owner files and MCP toggles."""
import json
from pathlib import Path

import pytest

from portable.runtime_data_paths import (
    migrate, normalize_legacy_environment, DataPathMigrationError,
    MEMORY_RELATIVE, UPLOAD_RELATIVE,
)

def _root(tmp_path):
    (tmp_path / "mai").mkdir()
    return tmp_path

def test_moves_old_db_and_uploads_preserving_bytes(tmp_path):
    root = _root(tmp_path)
    old_mem = root / "MAI_MEMORY"
    old_mem.mkdir()
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
    assert not (root / "MAI_MEMORY").exists()
    assert not (root / "mai_uploads").exists()
    assert not migrate(root)["memory"]

def test_refuses_collision_without_overwriting(tmp_path):
    root = _root(tmp_path)
    old = root / "MAI_MEMORY"
    old.mkdir()
    (old / "memory.sqlite3").write_bytes(b"old")
    destination = root / MEMORY_RELATIVE
    destination.mkdir(parents=True)
    (destination / "memory.sqlite3").write_bytes(b"new")
    with pytest.raises(DataPathMigrationError, match="Refusing to overwrite or merge"):
        migrate(root)
    assert (old / "memory.sqlite3").read_bytes() == b"old"
    assert (destination / "memory.sqlite3").read_bytes() == b"new"

def test_updates_only_known_old_memorizer_settings_preserves_flags(tmp_path):
    root = _root(tmp_path)
    config = root / "mai/mcp_tools/mcp_servers.json"
    config.parent.mkdir(parents=True)
    old_data = root / "MAI_MEMORY"
    old_data.mkdir()
    (old_data / "memory.sqlite3").write_bytes(b"sqlite")
    before = {
        "catalog_path": "mcp_catalog.json",
        "servers": [
            {"id": "memorizer", "enabled": False, "command": "python", 
             "args": ["-m", "mai.mcp_tools.MCP_memorizer.server"],
             "env": {"MEMORIZER_DATA_DIR": str(old_data)}},
            {"id": "other", "enabled": True, "command": "other",
             "env": {"MEMORIZER_DATA_DIR": "custom"}},
        ],
    }
    config.write_text(json.dumps(before), encoding="utf-8")
    assert migrate(root)["memorizer_config_updated"]
    after = json.loads(config.read_text(encoding="utf-8"))
    assert after["servers"][0]["enabled"] is False
    assert after["servers"][0]["args"][-1] == "mai.mcp_tools.mcp_memorizer.server"
    assert after["servers"][0]["env"]["MEMORIZER_DATA_DIR"] == str(root / MEMORY_RELATIVE)
    assert after["servers"][1] == before["servers"][1]
    assert (config.parent / "mcp_servers.pre-data-path-migration.json").exists()

def test_redirects_only_exact_old_default_env_paths(tmp_path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setenv("MEMORY_DB_PATH", str(root / "MAI_MEMORY/memory.sqlite3"))
    monkeypatch.setenv("MAI_UPLOAD_ROOT", str(root / "mai_uploads"))
    monkeypatch.setenv("CHAT_DB_PATH", str(root / "elsewhere/chat.sqlite3"))
    new_values = normalize_legacy_environment(root)
    assert new_values["MEMORY_DB_PATH"] == str(root / MEMORY_RELATIVE / "memory.sqlite3")
    assert new_values["MAI_UPLOAD_ROOT"] == str(root / UPLOAD_RELATIVE)
    assert "CHAT_DB_PATH" not in new_values
    assert (root / "mai").is_dir()


def test_legacy_mcp_config_inside_old_memory_folder_is_updated(tmp_path):
    root = _root(tmp_path)
    old_data = root / "MAI_MEMORY"
    old_data.mkdir()
    (old_data / "memory.sqlite3").write_bytes(b"old-memory")
    legacy_config = old_data / "mcp_servers.json"
    legacy_config.write_text(json.dumps({"servers": [{
        "id": "memorizer", "enabled": False,
        "command": "python",
        "args": ["-m", "mai.mcp_tools.MCP_memorizer.server"],
        "env": {"MEMORIZER_DATA_DIR": str(old_data)},
    }]}), encoding="utf-8")
    outcome = migrate(root)
    assert outcome["memory"] is True
    assert outcome["memorizer_config_updated"] is True
    migrated = root / MEMORY_RELATIVE / "mcp_servers.json"
    result = json.loads(migrated.read_text(encoding="utf-8"))["servers"][0]
    assert result["enabled"] is False
    assert result["env"]["MEMORIZER_DATA_DIR"] == str(root / MEMORY_RELATIVE)
    assert result["args"][-1] == "mai.mcp_tools.mcp_memorizer.server"
