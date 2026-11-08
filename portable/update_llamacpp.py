"""Update only MAI's pinned llama.cpp portable runtime (Windows / Ubuntu).

Never changes Python, uv, GGUF models, user databases, or application settings.
"""
from __future__ import annotations

import argparse
import hashlib
import time
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
import json
import re
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from urllib.request import Request, urlopen
import zipfile

from resolve_portable_sources import SOURCES, github_json, matching_assets



def select_stable_release(pattern: str):
    """Exclude explicitly flagged draft/prerelease builds; require a verified platform asset."""
    for page in range(1, 4):
        releases = github_json(f"/repos/ggml-org/llama.cpp/releases?per_page=100&page={page}")
        if not isinstance(releases, list):
            raise RuntimeError("Invalid GitHub release listing")
        for release in releases:
            if not isinstance(release, dict) or release.get("draft") is True or release.get("prerelease") is True:
                continue
            matches = matching_assets(release, pattern)
            if len(matches) > 1:
                raise RuntimeError(f"Ambiguous stable binaries in {release.get('tag_name')}")
            if len(matches) != 1:
                continue
            asset = matches[0]
            digest, url = asset.get("digest"), asset.get("browser_download_url")
            if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
                print(f"Stable {release.get('tag_name')} has no publisher SHA-256; skipping")
                continue
            if not isinstance(url, str) or not url.startswith("https://github.com/ggml-org/llama.cpp/releases/download/"):
                print(f"Stable {release.get('tag_name')} has invalid URL; skipping")
                continue
            return release, {"url": url, "sha256": digest.partition(":")[2].lower()}
        if len(releases) < 100:
            break
    return None


def release_date(release: dict):
    raw = release.get("published_at")
    if not isinstance(raw, str):
        return None
    try:
        date = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return date if date.tzinfo else None
    except ValueError:
        return None


def pinned_release(url: str):
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) != 6 or parts[:4] != ["ggml-org", "llama.cpp", "releases", "download"]:
        return None
    release = github_json(f"/repos/ggml-org/llama.cpp/releases/tags/{parts[4]}")
    return release if isinstance(release, dict) else None

def paths(root: Path) -> tuple[Path, Path, Path, str, str]:
    if sys.platform == "win32":
        return (
            root / "portable/portable-sources.json",
            root / "portable/portable-runtimes/llamacpp",
            root / "portable/portable-runtimes/python/python.exe",
            "llama-server.exe",
            "windows",
        )
    if sys.platform.startswith("linux"):
        osinfo = Path("/etc/os-release").read_text(encoding="utf-8")
        if not all(x in osinfo for x in ('ID=ubuntu', 'VERSION_ID="24.04"')) and not (
            'ID=ubuntu' in osinfo and 'VERSION_ID=24.04' in osinfo
        ):
            raise RuntimeError("Only Ubuntu 24.04 Linux is supported")
        if os.uname().machine != "x86_64":
            raise RuntimeError("Only Linux x86_64 is supported")
        ident = __import__("hashlib").sha256(str(root).encode()).hexdigest()[:24]
        cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "mai-portable" / ident
        return (
            root / "portable/portable-sources-linux.json",
            cache / "llamacpp",
            cache / "python/bin/python3",
            "llama-server",
            "linux",
        )
    raise RuntimeError("Unsupported OS")


def ensure_not_running() -> None:
    for port in (8000, 8080):
        with socket.socket() as sock:
            sock.settimeout(0.25)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} is active. Stop MAI/llama.cpp before updating.")


def validate_installed(runtime: Path, old: dict, platform: str, exe: str) -> bool:
    """Check receipt consistency without discarding previous runtimes."""
    if not (runtime / exe).is_file():
        raise RuntimeError(f"Existing llama.cpp executable missing: {runtime / exe}")
    if platform == "windows":
        receipt = runtime / ".mai-portable-source.json"
        try:
            record = json.loads(receipt.read_text(encoding="utf-8-sig"))
            matches = (
                record.get("url") == old["url"]
                and record.get("sha256", "").lower() == old["sha256"].lower()
                and record.get("executable") == exe
            )
        except (OSError, ValueError, AttributeError):
            matches = False
    else:
        receipt = runtime / ".mai-source-sha256"
        try:
            matches = receipt.read_text().strip().lower() == old["sha256"].lower()
        except OSError:
            matches = False
    if not matches:
        print(
            "WARNING: llama.cpp receipt differs from pinned manifest. "
            "Preserving the entire old runtime in a timestamped backup "
            "before installing a publisher-verified release.",
            file=sys.stderr,
        )
    return matches


def download(url: str, dest: Path, sha256: str) -> None:
    """Retry transient GitHub/CDN failures without accepting unverified bytes."""
    retry_codes = {408, 429, 500, 502, 503, 504}
    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        digest = hashlib.sha256()
        partial = dest.with_name(dest.name + ".part")
        partial.unlink(missing_ok=True)
        try:
            print(f"Downloading llama.cpp (attempt {attempt}/{max_attempts})", flush=True)
            request = Request(url, headers={
                "User-Agent": "MAI-llamacpp-update",
                "Accept": "application/octet-stream",
            })
            with urlopen(request, timeout=120) as response, partial.open("wb") as out:
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest().lower() != sha256.lower():
                raise RuntimeError("Downloaded llama.cpp SHA-256 does not match publisher digest")
            os.replace(partial, dest)
            return
        except (HTTPError, URLError, TimeoutError, ConnectionError, OSError) as exc:
            partial.unlink(missing_ok=True)
            if isinstance(exc, HTTPError) and exc.code not in retry_codes:
                raise RuntimeError(f"Download rejected with HTTP {exc.code}; not retrying") from exc
            if attempt == max_attempts:
                raise RuntimeError(
                    f"llama.cpp download failed after {max_attempts} attempts: {exc}. "
                    "The installed runtime and manifest are unchanged."
                ) from exc
            delay = min(2 ** attempt, 16)
            print(f"Temporary download error: {exc}; retrying in {delay}s", file=sys.stderr, flush=True)
            time.sleep(delay)
        finally:
            partial.unlink(missing_ok=True)



def verified_digest(path: Path, expected: str) -> bool:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest().lower() == expected.lower()


def download_with_curl(url: str, dest: Path, sha256: str) -> bool:
    """Use curl's GET redirect handling rather than a HEAD probe."""
    curl = shutil.which("curl.exe") if sys.platform == "win32" else None
    curl = curl or shutil.which("curl")
    if not curl:
        print("curl not installed; skipping alternate downloader", flush=True)
        return False
    partial = dest.with_name(dest.name + ".curl.part")
    partial.unlink(missing_ok=True)
    try:
        print("Trying curl GET download with browser-style headers", flush=True)
        cmd = [
            curl, "--fail", "--location", "--show-error", "--silent",
            "--retry", "2", "--retry-delay", "2", "--retry-all-errors",
            "--connect-timeout", "30", "--max-time", "600",
            "--user-agent", "Mozilla/5.0 MAI-llamacpp-updater",
            "--header", "Accept: application/octet-stream",
            "--output", str(partial), url,
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, timeout=650, check=False)
        if result.returncode != 0:
            print(f"curl download unavailable: {result.stderr[-400:]}", file=sys.stderr)
            return False
        if not verified_digest(partial, sha256):
            print("curl download hash mismatch; rejecting result", file=sys.stderr)
            return False
        os.replace(partial, dest)
        return True
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"curl download failed: {exc}", file=sys.stderr)
        return False
    finally:
        partial.unlink(missing_ok=True)


def download_from_github_asset_api(url: str, dest: Path, sha256: str) -> bool:
    """Use the official release asset API as a second transport path."""
    parsed = urlparse(url)
    fragments = parsed.path.strip("/").split("/")
    if parsed.hostname != "github.com" or len(fragments) != 6 or fragments[:2] != ["ggml-org", "llama.cpp"] or fragments[2] != "releases" or fragments[3] != "download":
        return False
    tag, filename = fragments[4:]
    try:
        release = github_json(f"/repos/ggml-org/llama.cpp/releases/tags/{tag}")
        assets = release.get("assets", []) if isinstance(release, dict) else []
        matches = [a for a in assets if isinstance(a, dict) and a.get("name") == filename]
        if len(matches) != 1 or not isinstance(matches[0].get("id"), int):
            print("Could not identify matching official release asset", file=sys.stderr)
            return False
        asset_url = f"https://api.github.com/repos/ggml-org/llama.cpp/releases/assets/{matches[0]['id']}"
        print("Trying official GitHub release asset API", flush=True)
        headers = {
            "User-Agent": "MAI-llamacpp-update",
            "Accept": "application/octet-stream",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        partial = dest.with_name(dest.name + ".api.part")
        partial.unlink(missing_ok=True)
        try:
            with urlopen(Request(asset_url, headers=headers), timeout=120) as response, partial.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            if not verified_digest(partial, sha256):
                print("GitHub API returned nonmatching bytes (possibly JSON); rejecting", file=sys.stderr)
                return False
            os.replace(partial, dest)
            return True
        finally:
            partial.unlink(missing_ok=True)
    except (OSError, ValueError, TimeoutError, URLError) as exc:
        print(f"GitHub asset API download failed: {exc}", file=sys.stderr)
        return False


def unpack(archive: Path, dest: Path) -> None:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    elif archive.name.endswith(".tar.gz"):
        with tarfile.open(archive, "r:gz") as t:
            # The archive is authenticated by the publisher's release SHA-256.
            t.extractall(dest, filter="data" if sys.version_info >= (3, 12) else None)
    else:
        raise RuntimeError(f"Unsupported llama.cpp archive: {archive.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    manifest, runtime, python, executable, platform = paths(root)
    if not python.is_file():
        raise RuntimeError("MAI portable Python missing; run setup first")
    if not manifest.is_file():
        raise RuntimeError("Pinned manifest missing; run setup first")
    ensure_not_running()
    config = json.loads(manifest.read_text(encoding="utf-8-sig"))
    old = config.get("llamacpp")
    if not isinstance(old, dict) or not old.get("url") or not old.get("sha256"):
        raise RuntimeError("Pinned manifest lacks valid llama.cpp entry")
    receipt_matches = validate_installed(runtime, old, platform, executable)
    pattern = SOURCES["llamacpp"][2] if platform == "windows" else r"llama-.*-bin-ubuntu-vulkan-x64\.tar\.gz"
    selected = select_stable_release(pattern)
    if selected is None:
        print(f"No stable release with a compatible {platform} Vulkan binary and official SHA-256. Keeping installed llama.cpp.")
        return 0
    stable, latest = selected
    if latest == old:
        print(f"Stable release {stable.get('tag_name')} already pinned; no update required.")
        return 0
    if not receipt_matches:
        print("Installed llama.cpp receipt differs from manifest; cannot safely rule out a downgrade. Keeping existing runtime.")
        return 0
    try:
        current = pinned_release(old["url"])
    except (HTTPError, URLError, OSError, ValueError) as exc:
        print(f"Could not verify current release age ({exc}); keeping existing runtime.")
        return 0
    current_date = release_date(current) if current else None
    stable_date = release_date(stable)
    if current_date is None or stable_date is None:
        print("Could not establish release chronology; keeping existing runtime to avoid downgrade.")
        return 0
    if stable_date <= current_date:
        print(f"Newest compatible stable {stable.get('tag_name')} is not newer than the installed release; keeping existing runtime.")
        return 0
    print(f"Selected newer stable release {stable.get('tag_name')}")
    print(f"Updating llama.cpp: {old['url']} -> {latest['url']}")
    runtime.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mai-llamacpp-update-", dir=runtime.parent) as tmp:
        temp = Path(tmp)
        filename = latest["url"].rsplit("/", 1)[-1]
        archive = temp / filename
        # Reuse a user-supplied official release archive, if available.
        # Unlike network retries, this also works when GitHub's CDN returns 504.
        cached = root / "portable/portable-downloads" / filename
        if cached.is_file():
            actual = hashlib.sha256(cached.read_bytes()).hexdigest()
            if actual.lower() != latest["sha256"].lower():
                raise RuntimeError(
                    f"Cached release SHA-256 mismatch: {cached}. "
                    "Remove the invalid archive and retry."
                )
            print(f"Using publisher-verified local llama.cpp archive: {cached}")
            shutil.copy2(cached, archive)
        else:
            # Different clients/endpoints may be routed differently by GitHub CDN.
            # Each successful transport must still pass the publisher digest.
            if not download_with_curl(latest["url"], archive, latest["sha256"]):
                if not download_from_github_asset_api(latest["url"], archive, latest["sha256"]):
                    try:
                        download(latest["url"], archive, latest["sha256"])
                    except RuntimeError as exc:
                        raise RuntimeError(
                            f"{exc} All automatic download paths failed. "
                            f"As a fallback, download {filename} from the official "
                            f"llama.cpp release page into {cached} and rerun update."
                        ) from exc
        extracted = temp / "extracted"
        extracted.mkdir()
        unpack(archive, extracted)
        matches = list(extracted.rglob(executable))
        if len(matches) != 1:
            raise RuntimeError(f"Expected exactly one {executable} in verified release, got {len(matches)}")
        staged = temp / "staged"
        shutil.copytree(matches[0].parent, staged)
        if platform == "windows":
            (staged / ".mai-portable-source.json").write_text(
                json.dumps({**latest, "executable": executable}, indent=2) + "\n", encoding="utf-8"
            )
        else:
            (staged / ".mai-source-sha256").write_text(latest["sha256"].lower(), encoding="utf-8")
            (staged / executable).chmod((staged / executable).stat().st_mode | 0o111)
        # Do not switch until the candidate can at least start and report its version.
        version_result = subprocess.run(
            [str(staged / executable), "--version"], cwd=staged, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        if version_result.returncode:
            code = version_result.returncode & 0xFFFFFFFF
            stdout = version_result.stdout.decode("utf-8", errors="replace")[-1000:]
            stderr = version_result.stderr.decode("utf-8", errors="replace")[-1000:]
            detail = (
                f"New llama-server failed preflight: exit 0x{code:08X}. "
                f"stdout={stdout!r}; stderr={stderr!r}. "
                "Existing llama.cpp installation and manifest were not changed."
            )
            if platform == "windows" and code == 0xC0E90002:
                detail += (
                    " Windows code integrity policy blocked executable/DLL loading. "
                    "Inspect Microsoft-Windows-CodeIntegrity/Operational events "
                    "(especially 3077/3033) to identify the blocked module. "
                    "Do not disable Windows security protections automatically."
                )
            raise RuntimeError(detail)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = runtime.with_name(runtime.name + ".backup-" + stamp)
        if backup.exists():
            raise RuntimeError(f"Backup path already exists: {backup}")
        new_config = dict(config)
        new_config["llamacpp"] = latest
        pending = temp / "manifest.json"
        pending.write_text(json.dumps(new_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Preserve the previous manifest for manual rollback/auditing.
        manifest_backup = manifest.with_name(manifest.name + ".backup-" + stamp)
        shutil.copy2(manifest, manifest_backup)
        os.replace(runtime, backup)
        try:
            os.replace(staged, runtime)
            # Same-filesystem replacement of a prepared manifest.
            new_manifest_tmp = manifest.with_name(manifest.name + ".pending-" + stamp)
            try:
                shutil.copy2(pending, new_manifest_tmp)
                os.replace(new_manifest_tmp, manifest)
            finally:
                new_manifest_tmp.unlink(missing_ok=True)
        except Exception:
            if runtime.exists():
                shutil.rmtree(runtime)
            os.replace(backup, runtime)
            shutil.copy2(manifest_backup, manifest)
            raise
        print("llama.cpp updated successfully. Runtime and manifest backups:")
        print(f"  {backup}")
        print(f"  {manifest_backup}")
        print("Run ./mai.sh check and ./mai.sh start; actual GPU inference is not tested here.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"llama.cpp update failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
