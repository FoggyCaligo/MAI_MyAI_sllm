#!/usr/bin/env bash
# Optional Ubuntu llama.cpp Portable router (unchanged Ollama launcher).
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo "Linux x86_64 required" >&2; exit 1; }
source /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || { echo "Ubuntu 24.04 required" >&2; exit 1; }
python3 "$root/portable/check-linux-gpu.py" --require
[[ -f "$root/.env" ]] || { echo "Missing .env" >&2; exit 1; }
id="$(printf %s "$root" | sha256sum | cut -c1-24)"
cache="$HOME/.cache/mai-portable/$id"
if [[ -n "${XDG_CACHE_HOME:-}" ]]; then cache="$XDG_CACHE_HOME/mai-portable/$id"; fi
python="$cache/python/bin/python3"
llama="$cache/llamacpp/bin/llama-server"
[[ -x "$python" && -x "$llama" ]] || { echo "Run prepare-portable-linux.sh --with-llamacpp first" >&2; exit 1; }
cd -- "$root"
if (echo >/dev/tcp/127.0.0.1/8080) >/dev/null 2>&1 || (echo >/dev/tcp/127.0.0.1/8000) >/dev/null 2>&1; then
  echo "Port 8080 or 8000 occupied" >&2; exit 1
fi
models_max="${1:-2}"
if [[ -z "$models_max" ]]; then models_max=2; fi
case "$models_max" in 1|2|3) ;; *) echo "Usage: bash portable/start-portable-llamacpp-linux.sh [1|2|3]" >&2; exit 2 ;; esac
mkdir -p "$cache/llamacpp"
ini="$(mktemp "$cache/llamacpp/mai-router.XXXXXX.ini")"
router_pid=""
cleanup() {
  if [[ -n "$router_pid" ]]; then
    kill -- "-$router_pid" 2>/dev/null || kill "$router_pid" 2>/dev/null || true
    wait "$router_pid" 2>/dev/null || true
  fi
  rm -f -- "$ini"
}
trap cleanup EXIT
flags="$("$python" - "$root" "$ini" <<'PY'
from pathlib import Path
import sys
from portable.resolve_llamacpp_settings import read_config
root = Path(sys.argv[1])
target = Path(sys.argv[2])
cfg = read_config(root / "settings.yaml")
def gguf(key, required=False):
    raw = cfg.get(key)
    if not raw:
        if required: raise ValueError("Missing " + key)
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute(): path = root / path
    path = path.resolve()
    if not path.is_file() or path.suffix.lower() != ".gguf":
        raise ValueError("Missing GGUF: " + key + ": " + str(path))
    with path.open("rb") as stream:
        if stream.read(4) != b"GGUF": raise ValueError("Invalid GGUF header: " + str(path))
    if "\n" in str(path) or "\r" in str(path):
        raise ValueError("Newlines in GGUF path not allowed")
    return path
main = gguf("LLAMACPP_MAIN_GGUF_PATH", True)
verifier = gguf("LLAMACPP_VERIFIER_GGUF_PATH", True)
projector = gguf("LLAMACPP_MAIN_MMPROJ_PATH")
vision = gguf("LLAMACPP_VISION_GGUF_PATH")
vision_projector = gguf("LLAMACPP_VISION_MMPROJ_PATH")
if vision_projector and not vision: raise ValueError("Vision projector requires Vision GGUF")
lines = ["version = 1", "[*]", "ctx-size = 8192", "n-gpu-layers = 99", "parallel = 1",
         "[mai-main]", "model = " + str(main)]
if projector: lines.append("mmproj = " + str(projector))
lines += ["[mai-verifier]", "model = " + str(verifier)]
if vision:
    lines += ["[mai-vision]", "model = " + str(vision)]
    if vision_projector: lines.append("mmproj = " + str(vision_projector))
target.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("true" if projector or cfg["LLAMACPP_MAIN_VISION"] else "false")
print("true" if vision else "false")
PY
)"
main_vision="$(printf '%s\n' "$flags" | sed -n 1p)"
separate_vision="$(printf '%s\n' "$flags" | sed -n 2p)"
export LLM_BACKEND=llamacpp MAIN_MODEL=mai-main VERIFIER_MODEL=mai-verifier
export LLAMACPP_HOST=http://127.0.0.1:8080 LLAMACPP_MAIN_VISION="$main_vision"
if [[ "$separate_vision" == true ]]; then
  export VISION_MODEL=mai-vision
  if (( models_max < 3 )); then echo "Warning: 3 models configured but models-max is 2" >&2; fi
elif [[ "$main_vision" == true ]]; then
  export VISION_MODEL=mai-main
else
  export VISION_MODEL=""
fi
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export PLAYWRIGHT_BROWSERS_PATH="$cache/playwright-browsers"
export MAI_CWD="$root" MAI_UPLOAD_ROOT="$root/mai_uploads"
export MEMORY_DB_PATH="$root/data/memory.sqlite3"
export CHAT_DB_PATH="$root/data/chat.sqlite3"
export LOG_DB_PATH="$root/data/log.sqlite3"
export SENTENCE_BREAKER_DB_PATH="$root/data/sentence_breaker.sqlite3"
export LD_LIBRARY_PATH="$cache/llamacpp/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
command -v setsid >/dev/null || { echo "Missing setsid" >&2; exit 1; }
setsid "$llama" --models-preset "$ini" --models-max "$models_max" --host 127.0.0.1 --port 8080 --jinja &
router_pid=$!
ready=0
for attempt in {1..120}; do
  kill -0 "$router_pid" 2>/dev/null || { echo "llama.cpp router exited" >&2; exit 1; }
  if curl -fsS --max-time 2 http://127.0.0.1:8080/v1/models 2>/dev/null |
     "$python" -c 'import json,sys; x=json.load(sys.stdin); ids={e["id"] for e in x["data"]}; assert {"mai-main","mai-verifier"}<=ids and (sys.argv[1]!="true" or "mai-vision" in ids)' "$separate_vision" 2>/dev/null; then
    ready=1; break
  fi
  sleep 1
done
[[ "$ready" == 1 ]] || { echo "llama.cpp model aliases not ready" >&2; exit 1; }
echo "MAI Linux llama.cpp router ready."
"$python" "$root/run_server.py"
