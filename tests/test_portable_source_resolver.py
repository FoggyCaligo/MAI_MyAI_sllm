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


def test_portable_rollback_drops_tev_requirement_but_retains_safe_upgrade():
    from pathlib import Path

    installer = Path("portable/prepare-portable.ps1").read_text(encoding="utf-8-sig")
    resolver = Path("portable/resolve_portable_sources.py").read_text(encoding="utf-8")
    readme = Path("portable/README.md").read_text(encoding="utf-8")
    assert "[switch]$UpgradeOllama" in installer
    assert "([bool]$UpgradeOllama)" in installer
    assert "/v1/systemone" not in installer
    assert "0.35.0" not in installer
    assert "tev1:4b" not in resolver
    assert "tev1:4b" not in readme
    assert "VERIFIER_DECISION_MODEL" not in readme
    assert "0.35.0" not in readme


def test_manifest_default_models_are_the_pre_tev_set(tmp_path):
    import json
    import sys
    from portable.resolve_portable_sources import main

    output = tmp_path / "sources.json"
    with patch("portable.resolve_portable_sources.resolve_one", return_value={
        "url": "https://example.test/pinned.zip", "sha256": "a" * 64,
    }):
        with patch.object(sys, "argv", ["resolve_portable_sources.py", "--output", str(output)]):
            main()
    assert json.loads(output.read_text(encoding="utf-8"))["models"] == [
        "ornith-1.5:9b", "lfm3-vl:2b", "granite4:3b-h",
    ]
