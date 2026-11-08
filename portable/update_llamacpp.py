"""Update only MAI's pinned llama.cpp portable runtime (Windows / Ubuntu).

Never changes Python, uv, GGUF models, user databases, or application settings.
"""
from __future__ import annotations

import argparse
import json
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

from resolve_portable_sources import resolve_one, SOURCES


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


def validate_installed(runtime: Path, old: dict, platform: str, exe: str) -> None:
    if not (runtime / exe).is_file():
        raise RuntimeError(f"Existing llama.cpp executable missing: {runtime / exe}")
    if platform == "windows":
        receipt = runtime / ".mai-portable-source.json"
        if not receipt.is_file():
            raise RuntimeError("Missing existing llama.cpp receipt; manual recovery required")
        record = json.loads(receipt.read_text(encoding="utf-8-sig"))
        if record.get("url") != old["url"] or record.get("sha256", "").lower() != old["sha256"].lower() or record.get("executable") != exe:
            raise RuntimeError("Existing llama.cpp receipt differs from pinned manifest")
    else:
        receipt = runtime / ".mai-source-sha256"
        if not receipt.is_file() or receipt.read_text().strip().lower() != old["sha256"].lower():
            raise RuntimeError("Existing llama.cpp receipt differs from pinned manifest")


def download(url: str, dest: Path, sha256: str) -> None:
    import hashlib
    h = hashlib.sha256()
    with urlopen(Request(url, headers={"User-Agent": "MAI-llamacpp-update"}), timeout=90) as response, dest.open("wb") as out:
        while data := response.read(1024 * 1024):
            out.write(data)
            h.update(data)
    if h.hexdigest().lower() != sha256.lower():
        raise RuntimeError("Downloaded llama.cpp SHA-256 does not match publisher digest")


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
    validate_installed(runtime, old, platform, executable)
    source = SOURCES["llamacpp"] if platform == "windows" else (
        "ggml-org", "llama.cpp", r"llama-.*-bin-ubuntu-vulkan-x64\.tar\.gz"
    )
    latest = resolve_one(*source)
    if latest == old:
        print("llama.cpp already matches the latest compatible published release.")
        return 0

    print(f"Updating llama.cpp: {old['url']} -> {latest['url']}")
    runtime.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mai-llamacpp-update-", dir=runtime.parent) as tmp:
        temp = Path(tmp)
        filename = latest["url"].rsplit("/", 1)[-1]
        archive = temp / filename
        download(latest["url"], archive, latest["sha256"])
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
        subprocess.run([str(staged / executable), "--version"], cwd=staged, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)

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
