#!/usr/bin/env bash
# Git Bash entry point. The existing MAI Python package directory is named mai/,
# so a root-level executable named mai cannot coexist with it.
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")" && pwd -P)"
if [[ "$(uname -s)" != MINGW* && "$(uname -s)" != MSYS* ]]; then
  printf 'This launcher requires Git Bash on Windows.\n' >&2
  exit 1
fi
if ! command -v cygpath >/dev/null 2>&1; then
  printf 'Git Bash cygpath is required to resolve the Windows script path.\n' >&2
  exit 1
fi
exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(cygpath -w "$root/mai.ps1")" "$@"
