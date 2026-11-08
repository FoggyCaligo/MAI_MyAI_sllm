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
models_max="${1:-1}"
[[ "$models_max" == 1 ]] || { echo "Single-resident router only: models-max must be 1" >&2; exit 2; }
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
catalog_json="$("$python" "$root/portable/llamacpp_model_catalog.py" --root "$root" --ini "$ini")"
default_model="$(printf '%s' "$catalog_json" | "$python" -c 'import json,sys; print(json.load(sys.stdin)["default"])')"
main_vision="$(printf '%s' "$catalog_json" | "$python" -c 'import json,sys; c=json.load(sys.stdin); print("true" if c["main_vision"] or c["projector"] else "false")')"
export LLM_BACKEND=llamacpp MAIN_MODEL="$default_model" VERIFIER_MODEL="$default_model"
export LLAMACPP_HOST=http://127.0.0.1:8080 LLAMACPP_MAIN_VISION="$main_vision"
export VISION_MODEL="$default_model"
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
     "$python" -c 'import json,sys; x=json.load(sys.stdin); ids={e["id"] for e in x["data"]}; assert {m["id"] for m in json.loads(sys.argv[1])["models"]} <= ids' "$catalog_json" 2>/dev/null; then
    ready=1; break
  fi
  sleep 1
done
[[ "$ready" == 1 ]] || { echo "llama.cpp model aliases not ready" >&2; exit 1; }
echo "MAI Linux llama.cpp router ready."
"$python" "$root/run_server.py"
