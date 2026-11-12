#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
case "$(uname -s)" in
  MINGW*|MSYS*)
    command -v cygpath >/dev/null 2>&1 || { echo "Git Bash cygpath required" >&2; exit 1; }
    exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(cygpath -w "$root/mai.ps1")" "$@"
    ;;
  Linux)
    ;;
  *)
    echo "Unsupported host OS: $(uname -s)" >&2
    exit 1
    ;;
esac
command="${1:-start}"
if (( $# > 0 )); then shift; fi
install_vulkan_option=0
if [[ "$command" == setup && "${1:-}" == "--install-vulkan" ]]; then
  install_vulkan_option=1
  shift
fi
if (( $# > 0 )); then echo "Unexpected arguments for $command: $*" >&2; exit 2; fi
case "$command" in
  restart)
    id="$(printf '%s' "$root" | sha256sum | cut -c1-24)"
    cache="${XDG_CACHE_HOME:-$HOME/.cache}/mai-portable/$id"
    python="$cache/python/bin/python3"
    [[ -x "$python" ]] || { echo "Portable Python missing; run ./mai.sh setup first" >&2; exit 1; }
    exec "$python" "$root/portable/restart_mai.py" --detach --update
    ;;
  end)
    id="$(printf '%s' "$root" | sha256sum | cut -c1-24)"
    cache="${XDG_CACHE_HOME:-$HOME/.cache}/mai-portable/$id"
    python="$cache/python/bin/python3"
    [[ -x "$python" ]] || { echo "Portable Python missing; run ./mai.sh setup first" >&2; exit 1; }
    exec "$python" "$root/portable/restart_mai.py" --stop
    ;;
  update)
    id="$(printf '%s' "$root" | sha256sum | cut -c1-24)"
    cache="${XDG_CACHE_HOME:-$HOME/.cache}/mai-portable/$id"
    python="$cache/python/bin/python3"
    [[ -x "$python" ]] || { echo "Portable Python missing; run ./mai.sh setup first" >&2; exit 1; }
    exec "$python" "$root/portable/update_llamacpp.py" --root "$root"
    ;;
  start)
    exec bash "$root/portable/start-portable-llamacpp-linux.sh"
    ;;
  check)
    python3 "$root/portable/check-linux-gpu.py" --require
    test -f "$root/.env" || { echo "Missing .env" >&2; exit 1; }
    id="$(printf '%s' "$root" | sha256sum | cut -c1-24)"
    cache="${XDG_CACHE_HOME:-$HOME/.cache}/mai-portable/$id"
    python="$cache/python/bin/python3"
    test -x "$python" || { echo "Portable Python missing; run ./mai.sh setup" >&2; exit 1; }
    "$python" "$root/portable/check_llamacpp_catalog.py" --root "$root"
    test -x "$cache/llamacpp/bin/llama-server" || { echo "llama.cpp runtime missing" >&2; exit 1; }
    ;;
  setup)
    mai_linux_setup() {
cd -- "$root"
# Provision Linux-native MAI into a host-local cache, leaving the NTFS SSD untouched.
set -euo pipefail

root="$root"
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
install_chromium=1
install_vulkan=$install_vulkan_option
with_llamacpp=1
# GPU driver support is explicit, not an automatic privileged mutation.
# AMD/Intel hosts can opt in to Ubuntu's Mesa Vulkan packages.
if [[ "$install_vulkan" == 1 ]]; then
  command -v sudo >/dev/null || { echo "sudo required for --install-vulkan" >&2; exit 1; }
  sudo apt-get update
  sudo apt-get install -y libvulkan1 mesa-vulkan-drivers vulkan-tools pciutils
fi
# Report the current driver state without requiring root for normal startup.
python3 "$root/portable/check-linux-gpu.py" --require

manifest="$root/portable/portable-sources-linux.json"
if [[ ! -f "$manifest" ]]; then
  python3 "$root/portable/resolve_portable_sources_linux.py"
fi
export MAI_LINUX_ROOT="$root"
export MAI_LINUX_MANIFEST="$manifest"
export MAI_LINUX_WITH_LLAMACPP="$with_llamacpp"
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
if os.environ.get("MAI_LINUX_WITH_LLAMACPP") == "1" and "llamacpp" not in data:
    raise ValueError("Manifest lacks llama.cpp; regenerate with --include-llamacpp")
names = ("python", "uv", "llamacpp")
for name in names:
    entry = data.get(name)
    if not isinstance(entry, dict) or not isinstance(entry.get("url"), str) or not isinstance(entry.get("sha256"), str):
        raise ValueError(f"Missing Linux source manifest entry: {name}")
    from urllib.parse import urlparse
    url = urlparse(entry["url"])
    if url.scheme != "https" or url.hostname != "github.com" or len(entry["sha256"]) != 64 or any(c not in "0123456789abcdefABCDEF" for c in entry["sha256"]):
        raise ValueError(f"Invalid pinned release URL or digest: {name}")
PY
archive_dir="$root/portable/portable-downloads/linux"
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
  elif [[ "$name" == llamacpp ]]; then
    local server
    server="$(find "$staging" -type f -name llama-server -print -quit)"
    [[ -n "$server" ]] || { echo "Missing llama-server in pinned Linux release" >&2; exit 1; }
    mkdir -p "$staging/runtime"
    cp -a -- "$(dirname "$server")" "$staging/runtime/bin"
    chmod +x "$staging/runtime/bin/llama-server"
  fi
  printf '%s' "$hash" > "$staging/runtime/.mai-source-sha256"
  mv -- "$staging/runtime" "$dest"
  rm -r -- "$staging"
}

runtimes=(uv python llamacpp)
for name in "${runtimes[@]}"; do
  file="$(fetch "$name")"
  digest="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]]["sha256"].lower())' "$manifest" "$name")"
  install_archive "$name" "$file" "$digest"
done

python="$cache/python/bin/python3"
uv="$cache/uv/bin/uv"
"$python" -c 'import sys; assert sys.version_info[:2] == (3,11); print(sys.version)'
"$uv" --version
if [[ "$with_llamacpp" == 1 ]]; then "$cache/llamacpp/bin/llama-server" --version; fi

# Never provision a Python environment on NTFS. Linux native packages live in host cache.
echo "[Linux Portable] Installing MAI Python dependencies"
UV_LINK_MODE=copy "$uv" pip install --python "$python" "$root[dev]"
"$python" -c 'import fastapi, uvicorn, playwright, mai'
if [[ "$install_chromium" == 1 ]]; then
  export PLAYWRIGHT_BROWSERS_PATH="$cache/playwright-browsers"
  "$python" -m playwright install chromium
fi
echo "[Linux Portable] Host runtime prepared: $cache"
if [[ "$with_llamacpp" == 1 ]]; then echo "llama.cpp available: portable/start-portable-llamacpp-linux.sh"; fi
echo "Run: ./mai.sh start"

  if [[ ! -f "$root/.env" ]]; then
    "$python" - "$root" <<'PY'
import getpass, json, os, pathlib, sys
root = pathlib.Path(sys.argv[1])
template = root / ".env.example"
if not template.is_file():
    raise FileNotFoundError(template)
with open("/dev/tty", "r", encoding="utf-8") as terminal:
    print("Owner login ID: ", end="", flush=True)
    owner_id = terminal.readline().strip()
if not owner_id:
    raise ValueError("Owner login ID cannot be empty")
password = getpass.getpass("Owner password: ")
if not password:
    raise ValueError("Owner password cannot be empty")
owner = json.dumps([{"user_id": owner_id, "user_pw": password, "db_id": "local-user"}], ensure_ascii=False)
lines = template.read_text(encoding="utf-8").splitlines()
result = []
for line in lines:
    if line.startswith("OWNER_USERS="):
        result.append("OWNER_USERS=" + owner)
    elif line.startswith("TRIAL_USERS="):
        result.append("TRIAL_USERS=[]")
    else:
        result.append(line)
target = root / ".env"
with target.open("x", encoding="utf-8") as f:
    f.write("\n".join(result) + "\n")
os.chmod(target, 0o600)
PY
  fi

    }
    mai_linux_setup
    ;;
  *)
    echo "Usage: ./mai.sh [start|restart|end|setup|check|update]" >&2; exit 2
    ;;
esac
