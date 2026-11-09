#!/bin/sh
set -eu

SERVER=""
SESSION=""
KEY=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --server) SERVER=$2; shift 2 ;;
    --session) SESSION=$2; shift 2 ;;
    --key) KEY=$2; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$SERVER" ] || [ -z "$SESSION" ] || [ -z "$KEY" ]; then
  echo "Usage: remote-agent-linux.sh --server https://... --session ... --key ..." >&2
  exit 2
fi

case "$SERVER" in
  https://*) ;;
  *) echo "HTTPS MAI server required" >&2; exit 2 ;;
esac

command -v python3 >/dev/null 2>&1 || {
  echo "Python 3.11+ is required on the remote Linux host" >&2
  exit 1
}
command -v curl >/dev/null 2>&1 || {
  echo "curl is required on the remote Linux host" >&2
  exit 1
}
command -v unzip >/dev/null 2>&1 || {
  echo "unzip is required on the remote Linux host" >&2
  exit 1
}
command -v sha256sum >/dev/null 2>&1 || {
  echo "sha256sum is required on the remote Linux host" >&2
  exit 1
}

PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "Python 3.11+ is required; found $PYVER" >&2
  exit 1
}

BASE="${XDG_DATA_HOME:-$HOME/.local/share}/mai/remote-worker"
TMPDIR_BASE="${TMPDIR:-/tmp}"
ZIP_PATH=$(mktemp "$TMPDIR_BASE/mai-remote-XXXXXX.zip")
HEADER_PATH=$(mktemp "$TMPDIR_BASE/mai-remote-headers-XXXXXX")
trap 'rm -f "$ZIP_PATH" "$HEADER_PATH"' EXIT INT TERM

BOOTSTRAP_URL="${SERVER%/}/remote/agent/$SESSION/bootstrap"
curl --fail --silent --show-error \
  -H "X-MAI-Agent-Secret: $KEY" \
  -D "$HEADER_PATH" \
  -o "$ZIP_PATH" \
  "$BOOTSTRAP_URL"

DIGEST=$(awk 'tolower($1)=="x-mai-bundle-sha256:" {print $2}' "$HEADER_PATH" | tr -d '\r' | tail -n 1)
if [ -z "$DIGEST" ]; then
  echo "MAI bootstrap response is missing X-MAI-Bundle-SHA256" >&2
  exit 1
fi
ACTUAL=$(sha256sum "$ZIP_PATH" | awk '{print $1}')
if [ "$ACTUAL" != "$DIGEST" ]; then
  echo "MAI worker bundle checksum mismatch" >&2
  exit 1
fi

TARGET="$BASE/$DIGEST"
INSTALL_READY="$TARGET/.installed-ok"

if [ ! -f "$INSTALL_READY" ]; then
  mkdir -p "$TARGET"
  unzip -oq "$ZIP_PATH" -d "$TARGET"
fi
# The bootstrap helper is stdlib-only and makes one reusable environment
# per remote dependency/Python contract, rather than per MAI source ZIP.
PYTHONPATH="$TARGET" python3 -m mai.remote.bootstrap_env --base "$BASE" --source "$TARGET"
ENV_KEY_FILE="$TARGET/.shared-env-key"
if [ ! -f "$ENV_KEY_FILE" ]; then
  echo "Remote dependency environment identity is missing" >&2
  exit 1
fi
ENV_DIGEST=$(cat "$ENV_KEY_FILE")
case "$ENV_DIGEST" in
  *[!0-9a-f]*|'')
    echo "Remote dependency environment identity is invalid" >&2
    exit 1 ;;
esac
if [ "${#ENV_DIGEST}" -ne 64 ]; then
  echo "Remote dependency environment identity is invalid" >&2
  exit 1
fi
VENV_PYTHON="$BASE/.shared-envs/$ENV_DIGEST/bin/python"
if [ ! -f "$VENV_PYTHON" ]; then
  echo "Remote dependency Python executable is missing: $VENV_PYTHON" >&2
  exit 1
fi

export MAI_REMOTE_CACHE_ROOT="$BASE"
export MAI_REMOTE_CACHE_DIGEST="$DIGEST"
export MAI_REMOTE_ENV_DIGEST="$ENV_DIGEST"
export PYTHONPATH="$TARGET"
export MAI_REMOTE_SERVER="$SERVER"
export MAI_REMOTE_SESSION="$SESSION"
export MAI_REMOTE_KEY="$KEY"
echo "MAI remote Python worker ready: $TARGET"
echo "Connected. Press Ctrl+C to stop accepting work."

exec "$VENV_PYTHON" -m mai.remote.agent_client
