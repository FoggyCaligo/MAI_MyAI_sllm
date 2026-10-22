"""Google API tool contracts, authorization boundary, and error propagation."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mai.tools import google_workspace as google
from mai.tools.registry import ToolRegistry


def test_google_tools_disabled_without_token_setting(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_TOKEN_PATH", raising=False)
    registry = ToolRegistry()
    google.register_google_tools(registry)
    assert registry.names() == ()


def test_google_tools_registered_when_configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_TOKEN_PATH", "not-a-real-token.json")
    registry = ToolRegistry()
    google.register_google_tools(registry)
    assert "google_drive_search" in registry.names()
    assert "google_sheets_read" in registry.names()
    assert "google_sheets_write" in registry.names()


def test_missing_oauth_file_is_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_OAUTH_TOKEN_PATH", str(tmp_path / "missing.json"))
    with pytest.raises(google.GoogleNotConfiguredError, match="does not exist"):
        google._credentials()


def test_read_uses_exact_spreadsheet_id_and_range(monkeypatch):
    service = MagicMock()
    service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
        "range": "Orders!A1:B2", "values": [["id", "amount"], ["A1", "100"]]
    }
    monkeypatch.setattr(google, "_service", lambda name, version: service)
    result = google.google_sheets_read("spreadsheet-123", "Orders!A1:B2")
    assert result["values"][1] == ["A1", "100"]
    service.spreadsheets.return_value.values.return_value.get.assert_called_once_with(
        spreadsheetId="spreadsheet-123", range="Orders!A1:B2"
    )


def test_write_preserves_values_and_option(monkeypatch):
    service = MagicMock()
    monkeypatch.setattr(google, "_service", lambda name, version: service)
    google.google_sheets_write("spreadsheet-123", "Sheet1!C3", [[42]], "RAW")
    service.spreadsheets.return_value.values.return_value.update.assert_called_once_with(
        spreadsheetId="spreadsheet-123", range="Sheet1!C3",
        valueInputOption="RAW", body={"values": [[42]]},
    )


def test_google_api_errors_are_not_converted_to_success(monkeypatch):
    service = MagicMock()
    service.spreadsheets.return_value.values.return_value.get.return_value.execute.side_effect = RuntimeError("API refused")
    monkeypatch.setattr(google, "_service", lambda name, version: service)
    with pytest.raises(RuntimeError, match="API refused"):
        google.google_sheets_read("sheet", "A1")
