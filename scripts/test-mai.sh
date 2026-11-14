#!/usr/bin/env bash
# MAI regression tests using the project's installed Python, not Windows Store aliases.
set -Eeuo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$root"
mode="${1:-all}"
if (( $# > 0 )); then shift; fi
if (( $# > 0 )); then echo "Unexpected arguments: $*" >&2; exit 2; fi

# Explicit override for developer virtual environments.
if [[ -n "${MAI_TEST_PYTHON:-}" ]]; then
  python_cmd=("$MAI_TEST_PYTHON")
elif [[ -x "$root/portable/portable-runtimes/python/python.exe" ]]; then
  python_cmd=("$root/portable/portable-runtimes/python/python.exe")
elif [[ -x "$root/.venv/Scripts/python.exe" ]]; then
  python_cmd=("$root/.venv/Scripts/python.exe")
elif [[ -x "$root/.venv/bin/python" ]]; then
  python_cmd=("$root/.venv/bin/python")
elif [[ "$(uname -s)" == Linux ]]; then
  cache_id="$(printf '%s' "$root" | sha256sum | cut -c1-24)"
  cache="${XDG_CACHE_HOME:-$HOME/.cache}/mai-portable/$cache_id"
  if [[ -x "$cache/python/bin/python3" ]]; then
    python_cmd=("$cache/python/bin/python3")
  elif command -v python3 >/dev/null 2>&1; then
    python_cmd=("$(command -v python3)")
  else
    echo "Python not found. Run ./mai.sh setup first." >&2; exit 127
  fi
elif command -v py.exe >/dev/null 2>&1 && py.exe -3.11 --version >/dev/null 2>&1; then
  python_cmd=(py.exe -3.11)
else
  echo "MAI Python not found. Run ./mai.sh setup, or set MAI_TEST_PYTHON to a Python executable." >&2
  exit 127
fi
echo "Using Python: ${python_cmd[*]}"
"${python_cmd[@]}" -m pytest --version >/dev/null || {
  echo "pytest is unavailable in the selected interpreter. Run ./mai.sh setup or install MAI dev dependencies." >&2
  exit 1
}
case "$mode" in
  contracts)
    exec "${python_cmd[@]}" -m pytest -q \
      tests/test_native_prompt_json_contract.py \
      tests/test_agent_tool_catalog_contract.py \
      tests/test_native_lazy_tools.py \
      tests/test_tool_runtime_context.py \
      tests/test_remote_model_context.py \
      tests/test_main_prompt_evidence_lookup.py \
      tests/test_terminal_tool_consolidation.py \
      tests/test_host_client_workspace_routing.py
    ;;
  all)
    exec "${python_cmd[@]}" -m pytest -q tests
    ;;
  *)
    echo "Usage: bash scripts/test-mai.sh [contracts|all]" >&2
    exit 2
    ;;
esac
