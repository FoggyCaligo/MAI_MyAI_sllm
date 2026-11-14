"""Official llama.cpp prereleases are eligible when the binary and digest match."""
from unittest.mock import patch

from portable.update_llamacpp import select_eligible_release


def _asset(tag: str, *, digest: str | None = None) -> dict:
    return {
        "name": f"llama-{tag}-bin-win-vulkan-x64.zip",
        "digest": digest if digest is not None else "sha256:" + "a" * 64,
        "browser_download_url": (
            f"https://github.com/ggml-org/llama.cpp/releases/download/{tag}/"
            f"llama-{tag}-bin-win-vulkan-x64.zip"
        ),
    }


def test_official_prerelease_with_digest_is_eligible():
    releases = [
        {"id": 1, "tag_name": "b12198", "draft": False, "prerelease": True,
         "assets": [_asset("b12198")]},
    ]
    with patch("portable.update_llamacpp.github_json", return_value=releases):
        result = select_eligible_release(r"llama-.*-bin-win-vulkan-x64\.zip")
    assert result is not None
    release, selected = result
    assert release["tag_name"] == "b12198"
    assert selected["sha256"] == "a" * 64


def test_draft_is_excluded_even_with_matching_binary():
    releases = [
        {"id": 2, "tag_name": "b12200", "draft": True, "prerelease": True,
         "assets": [_asset("b12200")]},
        {"id": 1, "tag_name": "b12198", "draft": False, "prerelease": True,
         "assets": [_asset("b12198")]},
    ]
    with patch("portable.update_llamacpp.github_json", return_value=releases):
        result = select_eligible_release(r"llama-.*-bin-win-vulkan-x64\.zip")
    assert result is not None
    assert result[0]["tag_name"] == "b12198"


def test_prerelease_without_publisher_digest_is_skipped():
    releases = [
        {"id": 2, "tag_name": "b12200", "draft": False, "prerelease": True,
         "assets": [_asset("b12200", digest="")]},
        {"id": 1, "tag_name": "b12198", "draft": False, "prerelease": True,
         "assets": [_asset("b12198")]},
    ]
    with patch("portable.update_llamacpp.github_json", return_value=releases):
        result = select_eligible_release(r"llama-.*-bin-win-vulkan-x64\.zip")
    assert result is not None
    assert result[0]["tag_name"] == "b12198"
