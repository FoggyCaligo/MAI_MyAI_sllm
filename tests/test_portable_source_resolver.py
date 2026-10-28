"""Release resolver checks official asset-bearing releases, not just /releases/latest."""
from unittest.mock import patch

from scripts.resolve_portable_sources import resolve_one


def test_llamacpp_skips_non_binary_latest_release():
    def github_json(path):
        if path == "/repos/ggml-org/llama.cpp/releases?per_page=100&page=1":
            return [{"id": 10, "tag_name": "v0.7.0", "draft": False},
                    {"id": 9, "tag_name": "b12345", "draft": False}]
        if path.endswith("/releases/10/assets?per_page=100&page=1"):
            return [{"name": "nightly-tag.txt"}]
        if path.endswith("/releases/9/assets?per_page=100&page=1"):
            return [{"name": "llama-b12345-bin-win-vulkan-x64.zip",
                     "digest": "sha256:" + "a" * 64,
                     "browser_download_url": "https://github.com/ggml-org/llama.cpp/releases/download/b12345/llama-b12345-bin-win-vulkan-x64.zip"}]
        raise AssertionError(path)

    with patch("scripts.resolve_portable_sources.github_json", side_effect=github_json):
        result = resolve_one("ggml-org", "llama.cpp", r"llama-.*-bin-win-vulkan-x64\.zip")
    assert result["sha256"] == "a" * 64
    assert "/download/b12345/" in result["url"]
