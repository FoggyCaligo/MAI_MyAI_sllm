#!/usr/bin/env bash
# Provision Linux-native MAI into a host-local cache, leaving the NTFS SSD untouched.
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
  echo "Ubuntu 24.04 x86-64 is required" >&2; exit 1
fi
source /etc/os-release
if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 24.04 ]]; then
  echo "Expected Ubuntu 24.04; found ${PRETTY_NAME:-unknown}" >&2; exit 1
fi
for utility in python3 curl tar sha256sum flock zstd git; do
  command -v "$utility" >/dev/null || { echo "Missing host prerequisite: $utility" >&2; exit 1; }
done
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != "--install-chromium" ) ]]; then
  echo "Usage: bash prepare-portable-linux.sh [--install-chromium]" >&2; exit 2
fi
manifest="$root/portable-sources-linux.json"
[[ -f "$manifest" ]] || { echo "Missing $manifest; create with scripts/resolve_portable_sources_linux.py" >&2; exit 1; }
export MAI_LINUX_ROOT="$root"
export MAI_LINUX_MANIFEST="$manifest"
cache_base="${XDG_CACHE_HOME:-$HOME/.cache}/mai-portable"
# Each SSD copy gets its own host cache; model/DB files remain on the SSD.
cache_id="$(printf '%s' "$root" | sha256sum | cut -c1-24)"
cache="$cache_base/$cache_id"
mkdir -p "$cache"
exec 9>"$cache/.install.lock"
flock -x 9
export MAI_LINUX_CACHE="$cache"
python3 - <<'PY'
import json
import os
from pathlib import Path
root = Path(os.environ["MAI_LINUX_MANIFEST"])
data = json.loads(root.read_text(encoding="utf-8"))
if data.get("schema_version") != 1 or data.get("target") != "ubuntu-24.04-amd64":
    raise ValueError("Linux manifest schema or target mismatch")
for name in ("python", "uv", "ollama"):
    entry = data.get(name)
    if not isinstance(entry, dict) or not isinstance(entry.get("url"), str) or not isinstance(entry.get("sha256"), str):
        raise ValueError(f"Missing Linux source manifest entry: {name}")
    from urllib.parse import urlparse
    url = urlparse(entry["url"])
    if url.scheme != "https" or url.hostname != "github.com" or len(entry["sha256"]) != 64 or any(c not in "0123456789abcdefABCDEF" for c in entry["sha256"]):
        raise ValueError(f"Invalid pinned release URL or digest: {name}")
PY
archive_dir="$root/portable-downloads/linux"
mkdir -p "$archive_dir"
fetch() {
  local name="$1" url hash file actual
  url="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]]["url"])' "$manifest" "$name")"
  hash="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]]["sha256"])' "$manifest" "$name")"
  file="$archive_dir/${url##*/}"
  if [[ ! -f "$file" ]]; then
    echo "[Linux Portable] Downloading $name" >&2
    curl --fail --location --show-error --output "$file.incomplete" "$url"
    mv -- "$file.incomplete" "$file"
  fi
  actual="$(sha256sum "$file" | cut -d' ' -f1)"
  [[ "$actual" == "${hash,,}" ]] || { echo "SHA-256 mismatch for $file" >&2; exit 1; }
  printf '%s' "$file"
}
install_archive() {
  local name="$1" archive="$2" hash="$3" staging="$cache/$name.incomplete" dest="$cache/$name" record="$cache/$name/.mai-source-sha256"
  if [[ -e "$dest" ]]; then
    [[ -f "$record" && "$(cat "$record")" == "$hash" ]] || { echo "Existing $name runtime is not validated: $dest" >&2; exit 1; }
    echo "[Linux Portable] Reusing $name"
    return
  fi
  [[ ! -e "$staging" ]] || { echo "Incomplete prior runtime: $staging" >&2; exit 1; }
  mkdir -p "$staging"
  echo "[Linux Portable] Extracting $name"
  case "$archive" in
    *.tar.gz) tar -xzf "$archive" -C "$staging" ;;
    *.tar.zst) tar --zstd -xf "$archive" -C "$staging" ;;
    *) echo "Unsupported Linux archive: $archive" >&2; exit 1 ;;
  esac
  if [[ "$name" == python ]]; then
    [[ -f "$staging/python/bin/python3" ]] || { echo "Missing Python interpreter in archive" >&2; exit 1; }
    mv -- "$staging/python" "$staging/runtime"
  elif [[ "$name" == uv ]]; then
    exe="$(find "$staging" -type f -name uv -print -quit)"
    [[ -n "$exe" ]] || { echo "Missing uv in archive" >&2; exit 1; }
    mkdir -p "$staging/runtime/bin"
    cp -- "$exe" "$staging/runtime/bin/uv"
    chmod +x "$staging/runtime/bin/uv"
  elif [[ "$name" == ollama ]]; then
    [[ -f "$staging/bin/ollama" ]] || { echo "Missing bin/ollama in archive" >&2; exit 1; }
    mv -- "$staging/bin" "$staging/runtime-bin"
    mkdir -p "$staging/runtime"
    mv -- "$staging/runtime-bin" "$staging/runtime/bin"
    if [[ -d "$staging/lib" ]]; then mv -- "$staging/lib" "$staging/runtime/lib"; fi
  fi
  printf '%s' "$hash" > "$staging/runtime/.mai-source-sha256"
  mv -- "$staging/runtime" "$dest"
  rm -r -- "$staging"
}

for name in uv python ollama; do
  file="$(fetch "$name")"
  digest="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]]["sha256"].lower())' "$manifest" "$name")"
  install_archive "$name" "$file" "$digest"
done

python="$cache/python/bin/python3"
uv="$cache/uv/bin/uv"
ollama="$cache/ollama/bin/ollama"
"$python" -c 'import sys; assert sys.version_info[:2] == (3,11); print(sys.version)'
"$uv" --version
"$ollama" --version

# Never provision a Python environment on NTFS. Linux native packages live in host cache.
echo "[Linux Portable] Installing MAI Python dependencies"
UV_LINK_MODE=copy "$uv" pip install --python "$python" "$root"
"$python" -c 'import fastapi, ollama, uvicorn, playwright, mai'
if [[ "${1:-}" == "--install-chromium" ]]; then
  export PLAYWRIGHT_BROWSERS_PATH="$cache/playwright-browsers"
  "$python" -m playwright install chromium
fi
echo "[Linux Portable] Host runtime prepared: $cache"
echo "Run: bash '$root/start-portable-linux.sh'"
