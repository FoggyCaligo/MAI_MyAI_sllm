"""Owner-issued download links work for paths outside the upload root."""

from pathlib import Path
from unittest.mock import patch

import pytest

from mai.app.downloads import DownloadLinkStore


def test_download_link_can_target_arbitrary_local_file(tmp_path: Path) -> None:
    target = tmp_path / "outside-uploads" / "project.zip"
    target.parent.mkdir()
    target.write_bytes(b"test")
    links = DownloadLinkStore(ttl_seconds=3600)

    result = links.create(str(target), base_url="https://mai.example.test/")

    assert result["url"].startswith("https://mai.example.test/download/")
    token = str(result["url"]).rsplit("/", 1)[-1]
    assert links.resolve(token) == target
    assert links.resolve("missing") is None


def test_download_link_expires(tmp_path: Path) -> None:
    target = tmp_path / "report.txt"
    target.write_text("report")
    links = DownloadLinkStore(ttl_seconds=10)
    with patch("mai.app.downloads.time.time", return_value=100.0):
        result = links.create(str(target))
    token = str(result["url"]).rsplit("/", 1)[-1]
    with patch("mai.app.downloads.time.time", return_value=110.0):
        assert links.resolve(token) is None


def test_missing_file_is_an_explicit_error(tmp_path: Path) -> None:
    links = DownloadLinkStore()
    with pytest.raises(FileNotFoundError):
        links.create(str(tmp_path / "missing.txt"))
