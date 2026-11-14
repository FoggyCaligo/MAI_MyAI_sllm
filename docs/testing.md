# MAI testing

From the repository root, with the development dependencies installed:

```bash
python -m pip install -e ".[dev]"
bash scripts/test-mai.sh contracts
bash scripts/test-mai.sh all
```

The script changes into the repository root automatically, so it can also be called from another directory. `contracts` checks native tool prompts, catalog JSON, schema validation, tool contexts, and host/client routing. `all` runs the entire `tests/` suite. Both return a nonzero exit status on failure.

Direct equivalent: `python -m pytest -q tests`. Run these in Git Bash on Windows or Bash on Linux. The GitHub Actions native regression workflow also runs the entire suite on Windows and Ubuntu. CI success must be checked on the exact commit under test, not an earlier branch revision.
