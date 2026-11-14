#!/usr/bin/env bash
# Run from any directory. Requires the project's Python environment with pytest installed.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
mode="${1:-all}"
case "$mode" in
  contracts)
    python -m pytest -q \
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
    python -m pytest -q tests
    ;;
  *)
    echo "Usage: bash scripts/test-mai.sh [contracts|all]" >&2
    exit 2
    ;;
esac
