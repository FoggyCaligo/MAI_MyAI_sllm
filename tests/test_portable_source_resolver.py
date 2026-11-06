"""Release resolver checks official asset-bearing releases, not just /releases/latest."""
from unittest.mock import patch

from portable.resolve_portable_sources import resolve_one


def test_llamacpp_skips_non_binary_latest_release():
    def github_json(path):
        if path == "/repos/ggml-org/llama.cpp/releases?per_page=100&page=1":
            return [
                {"id": 10, "tag_name": "v0.7.0", "draft": False,
                 "assets": [{"name": "nightly-tag.txt"}]},
                {"id": 9, "tag_name": "b12345", "draft": False,
                 "assets": [{"name": "llama-b12345-bin-win-vulkan-x64.zip",
                              "digest": "sha256:" + "a" * 64,
                              "browser_download_url": "https://github.com/ggml-org/llama.cpp/releases/download/b12345/llama-b12345-bin-win-vulkan-x64.zip"}]},
            ]
        raise AssertionError(path)

    with patch("portable.resolve_portable_sources.github_json", side_effect=github_json):
        result = resolve_one("ggml-org", "llama.cpp", r"llama-.*-bin-win-vulkan-x64\.zip")
    assert result["sha256"] == "a" * 64
    assert "/download/b12345/" in result["url"]


def test_ollama_release_rejects_versions_older_than_system_one():
    import pytest
    from portable.resolve_portable_sources import ollama_release_version
    assert ollama_release_version({"tag_name": "v0.35.0"}) == (0, 35, 0)
    assert ollama_release_version({"tag_name": "v0.36.1"}) == (0, 36, 1)
    with pytest.raises(ValueError, match="older than"):
        ollama_release_version({"tag_name": "v0.34.9"})
    with pytest.raises(ValueError, match="version tag"):
        ollama_release_version({"tag_name": "latest"})


def test_resolver_rejects_old_official_ollama_release():
    import pytest
    from portable.resolve_portable_sources import resolve_one
    payload = {
        "id": 17,
        "tag_name": "v0.34.9",
        "assets": [{
            "name": "ollama-windows-amd64.zip",
            "digest": "sha256:" + "a" * 64,
            "browser_download_url":
                "https://github.com/ollama/ollama/releases/download/v0.34.9/ollama-windows-amd64.zip",
        }],
    }
    with patch("portable.resolve_portable_sources.github_json", return_value=payload):
        with pytest.raises(ValueError, match="older than"):
            resolve_one("ollama", "ollama", r"ollama-windows-amd64\\.zip")


def test_new_portable_manifest_includes_tev1(tmp_path):
    import json
    import sys
    from portable.resolve_portable_sources import main

    def resolve(*args):
        return {"url": "https://example.test/release.zip", "sha256": "a" * 64}

    output = tmp_path / "portable-sources.json"
    with patch("portable.resolve_portable_sources.resolve_one", side_effect=resolve):
        with patch.object(sys, "argv", ["resolve_portable_sources.py", "--output", str(output)]):
            main()
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert "tev1:4b" in manifest["models"]


def test_portable_installer_has_explicit_systemone_version_contract():
    from pathlib import Path

    script = Path("portable/prepare-portable.ps1").read_text(encoding="utf-8-sig")
    assert "[switch]$UpgradeOllama" in script
    assert "0.35.0" in script
    assert "Install-VerifiedArchive $config.ollama" in script
    assert "([bool]$UpgradeOllama)" in script
    assert "Preserved previous Ollama runtime" in script
