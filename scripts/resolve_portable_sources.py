"""Resolve official GitHub release assets into an immutable, SHA-256-pinned manifest.

This command fetches publisher-declared digests from GitHub release asset metadata.
It does not treat a digest computed after download as independent authentication.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from urllib.request import Request, urlopen


SOURCES = {
    "python": ("astral-sh", "python-build-standalone", r"cpython-3\.11\.[0-9]+\+[^/]+-x86_64-pc-windows-msvc-install_only\.tar\.gz"),
    "uv": ("astral-sh", "uv", r"uv-x86_64-pc-windows-msvc\.zip"),
    "ollama": ("ollama", "ollama", r"ollama-windows-amd64\.zip"),
    "llamacpp": ("ggml-org", "llama.cpp", r"llama-.*-bin-win-vulkan-x64\.zip"),
}


def github_json(path: str) -> object:
    url = "https://api.github.com" + path
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "MAI-portable-manifest"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)
    with urlopen(req, timeout=30) as response:
        return json.load(response)



def matching_assets(release: dict[str, object], asset_pattern: str) -> list[dict[str, object]]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("GitHub release response is missing assets")
    return [
        asset for asset in assets
        if isinstance(asset, dict)
        and isinstance(asset.get("name"), str)
        and re.fullmatch(asset_pattern, asset["name"]) is not None
    ]


def latest_binary_release(owner: str, repo: str, asset_pattern: str) -> tuple[dict[str, object], dict[str, object]]:
    """Find the newest release with a matching asset without N+1 asset API calls."""
    for page in range(1, 4):
        releases = github_json(f"/repos/{owner}/{repo}/releases?per_page=100&page={page}")
        if not isinstance(releases, list):
            raise ValueError(f"Invalid release listing: {owner}/{repo}")
        for release in releases:
            if not isinstance(release, dict) or not isinstance(release.get("id"), int):
                raise ValueError(f"Malformed release entry: {owner}/{repo}")
            if release.get("draft") is True:
                continue
            matches = matching_assets(release, asset_pattern)
            if len(matches) > 1:
                raise ValueError(f"Ambiguous Windows Vulkan assets in {owner}/{repo} release {release.get('tag_name')}")
            if matches:
                print(f"Selected {owner}/{repo} release {release.get('tag_name')} with binary {matches[0]['name']}")
                return release, matches[0]
        if len(releases) < 100:
            break
    raise ValueError(f"No matching binary asset found in recent {owner}/{repo} releases")


def resolve_one(owner: str, repo: str, asset_pattern: str) -> dict[str, str]:
    if (owner, repo) == ("ggml-org", "llama.cpp"):
        _, asset = latest_binary_release(owner, repo, asset_pattern)
    else:
        release = github_json(f"/repos/{owner}/{repo}/releases/latest")
        if not isinstance(release, dict) or not isinstance(release.get("id"), int):
            raise ValueError(f"Invalid release metadata: {owner}/{repo}")
        matches = matching_assets(release, asset_pattern)
        if len(matches) != 1:
            raise ValueError(f"Expected one matching asset for {owner}/{repo}, got {len(matches)}")
        asset = matches[0]
    digest = asset.get("digest")
    url = asset.get("browser_download_url")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest):
        raise ValueError(f"Official release does not publish usable SHA-256: {owner}/{repo}/{asset['name']}")
    if not isinstance(url, str) or not url.startswith(f"https://github.com/{owner}/{repo}/releases/download/"):
        raise ValueError(f"Unexpected asset URL: {owner}/{repo}/{asset['name']}")
    return {"url": url, "sha256": digest.partition(":")[2].lower()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("portable-sources.json"))
    parser.add_argument("--include-llamacpp", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite pinned manifest: {args.output}")
    config: dict[str, object] = {"schema_version": 1}
    selected = ("python", "uv", "ollama", "llamacpp") if args.include_llamacpp else ("python", "uv", "ollama")
    for name in selected:
        config[name] = resolve_one(*SOURCES[name])
    config["models"] = ["ornith-1.5:9b", "gemma4:e4b"]
    args.output.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Publisher-digest-pinned manifest saved: {args.output}")


if __name__ == "__main__":
    main()
