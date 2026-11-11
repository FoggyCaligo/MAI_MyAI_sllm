#!/usr/bin/env bash
# Ubuntu llama.cpp Portable router.
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
if (echo >/dev/tcp/127.0.0.1/8080) >/dev/null 2>&1 || (echo >/dev/tcp/127.0.0.1/8081) >/dev/null 2>&1 || (echo >/dev/tcp/127.0.0.1/8000) >/dev/null 2>&1; then
  echo "Port 8080, 8081 or 8000 occupied" >&2; exit 1
fi
models_max="${1:-1}"
[[ "$models_max" == 1 ]] || { echo "Single-resident router only: models-max must be 1" >&2; exit 2; }
mkdir -p "$cache/llamacpp"
ini="$(mktemp "$cache/llamacpp/mai-router.XXXXXX.ini")"
router_pid=""
gateway_pid=""
cleanup() {
  if [[ -n "$gateway_pid" ]]; then
    kill -- "-$gateway_pid" 2>/dev/null || kill "$gateway_pid" 2>/dev/null || true
    wait "$gateway_pid" 2>/dev/null || true
  fi
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
reasoning_preserve="$(printf '%s' "$catalog_json" | "$python" -c 'import json,sys; print("true" if json.load(sys.stdin)["reasoning_preserve"] else "false")')"
cache_ram_mib="$(printf '%s' "$catalog_json" | "$python" -c 'import json,sys; print(json.load(sys.stdin)["cache_ram_mib"])')"
min_free_vram_mib="$(printf '%s' "$catalog_json" | "$python" -c 'import json,sys; print(json.load(sys.stdin)["min_free_vram_mib"])')"
guard_enabled="$(printf '%s' "$catalog_json" | "$python" -c 'import json,sys; print("true" if json.load(sys.stdin)["memory_guard_enabled"] else "false")')"
min_available_ram_mib="$(printf '%s' "$catalog_json" | "$python" -c 'import json,sys; print(json.load(sys.stdin)["min_available_ram_mib"])')"
export LLAMACPP_MEMORY_GUARD_ENABLED="$guard_enabled"
router_port=8080
if [[ "$guard_enabled" == true ]]; then router_port=8081; fi
export LLAMACPP_MIN_FREE_VRAM_MIB="$min_free_vram_mib"
export MAI_MIN_AVAILABLE_RAM_MIB="$min_available_ram_mib"
export LLAMA_ARG_REASONING_PRESERVE="$reasoning_preserve"
export LLAMA_ARG_CACHE_RAM="$cache_ram_mib"
reasoning_preserve_flag="--no-reasoning-preserve"
if [[ "$reasoning_preserve" == true ]]; then reasoning_preserve_flag="--reasoning-preserve"; fi
export LLM_BACKEND=llamacpp MAIN_MODEL="$default_model"
export LLAMACPP_HOST=http://127.0.0.1:8080 LLAMACPP_MAIN_VISION="$main_vision"
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export PLAYWRIGHT_BROWSERS_PATH="$cache/playwright-browsers"
export MAI_CWD="$root" MAI_UPLOAD_ROOT="$root/mai_uploads"
export MEMORY_DB_PATH="$root/MAI_MEMORY/memory.sqlite3"
export CHAT_DB_PATH="$root/MAI_MEMORY/chat.sqlite3"
export LOG_DB_PATH="$root/MAI_MEMORY/log.sqlite3"
export SENTENCE_BREAKER_DB_PATH="$root/MAI_MEMORY/sentence_breaker.sqlite3"
export LD_LIBRARY_PATH="$cache/llamacpp/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
command -v setsid >/dev/null || { echo "Missing setsid" >&2; exit 1; }
margin_args=()
if [[ "$guard_enabled" == true ]]; then margin_args=(--models-memory-margin "$min_free_vram_mib"); fi
setsid "$llama" --models-preset "$ini" --models-max "$models_max" "${margin_args[@]}" --host 127.0.0.1 --port "$router_port" --jinja "$reasoning_preserve_flag" &
router_pid=$!
ready=0
for attempt in {1..120}; do
  kill -0 "$router_pid" 2>/dev/null || { echo "llama.cpp router exited" >&2; exit 1; }
  if curl -fsS --max-time 2 "http://127.0.0.1:$router_port/v1/models" 2>/dev/null |
     "$python" -c 'import json,sys; x=json.load(sys.stdin); ids={e["id"] for e in x["data"]}; assert {m["id"] for m in json.loads(sys.argv[1])["models"]} <= ids' "$catalog_json" 2>/dev/null; then
    ready=1; break
  fi
  sleep 1
done
[[ "$ready" == 1 ]] || { echo "llama.cpp model aliases not ready" >&2; exit 1; }
if [[ "$guard_enabled" == true ]]; then
  setsid "$python" -m portable.llamacpp_memory_gateway --upstream http://127.0.0.1:8081 --port 8080 --min-free-vram-mib "$min_free_vram_mib" --min-available-ram-mib "$min_available_ram_mib" --telemetry-path "$root/MAI_MEMORY/gpu_ram_safety.jsonl" &
  gateway_pid=$!
  gateway_ready=0
  for attempt in {1..30}; do
    kill -0 "$gateway_pid" 2>/dev/null || { echo "Memory gateway exited" >&2; exit 1; }
    if curl -fsS --max-time 2 http://127.0.0.1:8080/v1/models >/dev/null 2>&1; then gateway_ready=1; break; fi
    sleep 1
  done
  [[ "$gateway_ready" == 1 ]] || { echo "Memory gateway not ready" >&2; exit 1; }
fi
echo "MAI Linux llama.cpp router ready; shared memory guard: $guard_enabled."
"$python" "$root/run_server.py"
