#!/usr/bin/env bash
# Ubuntu portable single-model llama.cpp runner (host-cached binary, SSD GGUF).
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo "Linux x86-64 required" >&2; exit 1; }
source /etc/os-release
[[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04 ]] || { echo "Ubuntu 24.04 required" >&2; exit 1; }
# Diagnose hardware Vulkan support before the model runtime is started.
python3 "$root/portable/check-linux-gpu.py" --require

[[ -f "$root/.env" ]] || { echo "Missing .env" >&2; exit 1; }
id="$(printf '%s' "$root" | sha256sum | cut -c1-24)"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/mai-portable/$id"
python="$cache/python/bin/python3"
llama="$cache/llamacpp/bin/llama-server"
[[ -x "$python" && -x "$llama" ]] || { echo "Missing Linux runtime; run prepare-portable-linux.sh" >&2; exit 1; }
cd -- "$root"
gguf="${1:-}"
if [[ -z "$gguf" ]]; then
  gguf="$("$python" -c 'import yaml,pathlib; print(yaml.safe_load(pathlib.Path("settings.yaml").read_text(encoding="utf-8-sig"))["MODEL_GGUF_PATH"])')"
fi
[[ "$gguf" = /* ]] || gguf="$root/$gguf"
[[ -f "$gguf" && "$gguf" == *.gguf ]] || { echo "GGUF file is missing: $gguf" >&2; exit 1; }
# Optional model paths come from the same validated settings.yaml resolver
# used by Windows Portable.
model_config="$("$python" "$root/portable/resolve_runtime_models.py" "$root/settings.yaml" "$root")"
mapfile -t model_paths < <(printf '%s' "$model_config" | "$python" -c 'import json,sys; d=json.load(sys.stdin); [print(d[k]) for k in ("VISION_GGUF_PATH","VISION_MMPROJ_PATH","VERIFIER_GGUF_PATH")]')
vision="${model_paths[0]:-}"
projector="${model_paths[1]:-}"
verifier="${model_paths[2]:-}"
router_mode=false
[[ -n "$vision" || -n "$verifier" ]] && router_mode=true
alias_name="mai-model"
if (echo >/dev/tcp/127.0.0.1/8080) >/dev/null 2>&1; then
  echo "Port 8080 already occupied" >&2; exit 1
fi
if (echo >/dev/tcp/127.0.0.1/8000) >/dev/null 2>&1; then
  echo "MAI port 8000 already occupied" >&2; exit 1
fi
export MAIN_MODEL="$alias_name" LLAMACPP_HOST="http://127.0.0.1:8080" LLM_BACKEND="llamacpp"
export MAI_ROUTER_MODE="$router_mode"
export MAI_VISION_MODEL=""
export MAI_VERIFIER_MODEL=""
[[ -n "$vision" ]] && export MAI_VISION_MODEL="mai-vision"
[[ -n "$verifier" ]] && export MAI_VERIFIER_MODEL="mai-verifier"
export MAI_ACTIVE_GGUF_PATH="$gguf"
export MAI_SLOT_CACHE_DIR="$root/portable/portable-slot-cache"
mkdir -p -- "$MAI_SLOT_CACHE_DIR"
unset VERIFIER_MODEL VISION_MODEL OLLAMA_HOST OLLAMA_MODELS || true
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export PLAYWRIGHT_BROWSERS_PATH="$cache/playwright-browsers"
export MAI_CWD="$root" MAI_UPLOAD_ROOT="$root/mai_uploads"
export MEMORY_DB_PATH="$root/data/memory.sqlite3"
export CHAT_DB_PATH="$root/data/chat.sqlite3"
export LOG_DB_PATH="$root/data/log.sqlite3"
export SENTENCE_BREAKER_DB_PATH="$root/data/sentence_breaker.sqlite3"
export LD_LIBRARY_PATH="$(dirname "$llama"):${LD_LIBRARY_PATH:-}"
llama_pid=""
preset=""
cleanup() {
  if [[ -n "$llama_pid" ]]; then kill "$llama_pid" 2>/dev/null || true; wait "$llama_pid" 2>/dev/null || true; fi
  if [[ -n "$preset" ]]; then rm -f -- "$preset"; fi
}
trap cleanup EXIT
if [[ "$router_mode" == true ]]; then
  preset="$MAI_SLOT_CACHE_DIR/router-models-$$.ini"
  {
    printf '[mai-model]\\nmodel = %s\\nparallel = 1\\n' "$gguf"
    if [[ -n "$vision" ]]; then
      printf '\\n[mai-vision]\\nmodel = %s\\nparallel = 1\\n' "$vision"
      if [[ -n "$projector" ]]; then printf 'mmproj = %s\\n' "$projector"; fi
    fi
    if [[ -n "$verifier" ]]; then printf '\\n[mai-verifier]\\nmodel = %s\\nparallel = 1\\n' "$verifier"; fi
  } > "$preset"
  chmod 600 "$preset"
  "$llama" --models-preset "$preset" --models-max 1 --host 127.0.0.1 --port 8080 --jinja &
else
  "$llama" -m "$gguf" --alias "$alias_name" --host 127.0.0.1 --port 8080 --jinja --parallel 1 --slot-save-path "$MAI_SLOT_CACHE_DIR" &
fi
llama_pid=$!
ready=0
for attempt in {1..100}; do
  kill -0 "$llama_pid" 2>/dev/null || { echo "llama.cpp exited before ready" >&2; exit 1; }
  if curl --silent --fail --max-time 1 http://127.0.0.1:8080/v1/models >/dev/null; then ready=1; break; fi
  sleep 1
done
[[ "$ready" == 1 ]] || { echo "llama.cpp did not become ready" >&2; exit 1; }
"$python" "$root/run_server.py"
