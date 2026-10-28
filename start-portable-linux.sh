#!/usr/bin/env bash
# Run MAI on Ubuntu 24.04 using host-cached Linux binaries and SSD-backed data.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo "Linux x86-64 required" >&2; exit 1; }
source /etc/os-release
[[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04 ]] || { echo "Ubuntu 24.04 required" >&2; exit 1; }
[[ -f "$root/.env" ]] || { echo "Missing .env at $root" >&2; exit 1; }
[[ -d "$root/portable-models/ollama/manifests" ]] || { echo "Missing Ollama model manifests on SSD" >&2; exit 1; }
id="$(printf '%s' "$root" | sha256sum | cut -c1-24)"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/mai-portable/$id"
python="$cache/python/bin/python3"
ollama="$cache/ollama/bin/ollama"
[[ -f "$python" && -f "$ollama" ]] || { echo "Linux runtime missing; run: bash '$root/prepare-portable-linux.sh'" >&2; exit 1; }
# Fail on existing Ollama rather than silently talking to another device/model.
if (echo >/dev/tcp/127.0.0.1/11434) >/dev/null 2>&1; then
  echo "Port 11434 is occupied; stop existing Ollama first" >&2; exit 1
fi
if (echo >/dev/tcp/127.0.0.1/8000) >/dev/null 2>&1; then
  echo "MAI port 8000 is occupied" >&2; exit 1
fi
export OLLAMA_HOST="http://127.0.0.1:11434"
export OLLAMA_MODELS="$root/portable-models/ollama"
export OLLAMA_NO_CLOUD=true
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export PLAYWRIGHT_BROWSERS_PATH="$cache/playwright-browsers"
export MAI_CWD="$root" MAI_UPLOAD_ROOT="$root/mai_uploads"
export MEMORY_DB_PATH="$root/data/memory.sqlite3"
export CHAT_DB_PATH="$root/data/chat.sqlite3"
export LOG_DB_PATH="$root/data/log.sqlite3"
export SENTENCE_BREAKER_DB_PATH="$root/data/sentence_breaker.sqlite3"
export LLM_BACKEND="ollama"
cd -- "$root"
ollama_pid=""
cleanup() {
  if [[ -n "$ollama_pid" ]]; then
    kill "$ollama_pid" 2>/dev/null || true
    wait "$ollama_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT
"$ollama" serve &
ollama_pid=$!
ready=0
for attempt in {1..80}; do
  if ! kill -0 "$ollama_pid" 2>/dev/null; then
    echo "Portable Ollama exited during startup" >&2; exit 1
  fi
  if curl --silent --fail --max-time 1 http://127.0.0.1:11434/api/tags >/dev/null; then
    ready=1
    break
  fi
  sleep 0.5
done
[[ "$ready" == 1 ]] || { echo "Portable Ollama did not become ready" >&2; exit 1; }
echo "[Linux Portable] Ollama ready; launching MAI on http://127.0.0.1:8000"
"$python" "$root/run_server.py"
