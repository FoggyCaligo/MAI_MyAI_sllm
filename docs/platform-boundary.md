# dev OS 경계 — Windows / Linux

> 이 문서는 구현된 OS 분리와 검증 경계를 설명한다. Linux 원격 워커는 현재 소스에 구현되어 있으나 운영체제별 실기기 검증은 별도이다.

## 목적

MAI Agent, verifier, memory, tool contracts, GGUF/SQLite are host-neutral.
The OS boundary lives in `mai/platform/`, and deployment scripts supply
OS-specific Python, llama.cpp Vulkan binary, drivers, and system integrations.

```text
MAI agent / verifier / memory
  -> local tool registry / remote native worker (unchanged schema)
  -> terminal_run
  -> current_platform()
      -> WindowsPlatform: CMD/PowerShell, UTF-8, Windows process groups
      -> LinuxPlatform: /bin/sh, POSIX sessions/process groups
```

OS detection happens at runtime on the *executing* machine (including a remote
worker), not based on the requesting browser or a hard-coded prompt. Unsupported
platforms fail with an explicit error, not a silent Linux fallback.

## Scope of this change

- Terminal subprocess invocation, Unicode encoding, script workspaces,
  shell identity and process termination are isolated behind a common contract.
- Windows `_powershell_command` and `_windows_cmd_args` remain importable
  compatibility shims. Existing tool names, arguments and failure signaling
  remain unchanged.
- General file operations already use `pathlib` / `shutil` and do not need
  duplicate Windows/Linux implementations.
- This is **not** a proof that all optional features (Tailscale, Playwright,
  microphone/audio, remote clients, firewall, GPU acceleration) work on both
  operating systems. Those need independent integration testing.

## Portable host binaries are still OS-specific

Windows x64: `portable/start-portable.ps1` uses bundled Windows Python and
llama.cpp Vulkan binaries.

Linux x64: `portable/start-portable-linux.sh` uses the previously prepared
Ubuntu 24.04 host cache for Linux Python/llama.cpp binaries and reads the GGUF
on the portable drive. Do not copy Windows `.exe` files as though they run
natively on Linux.

One GGUF file and SQLite data may be portable; executables and GPU drivers
are not interchangeable. Shut down MAI before migrating live SQLite files,
and back up running databases with SQLite's online backup interface rather
than blindly copying changing files.

## Validation

```shell
python -m pytest -q tests/test_platform_adapters.py tests/test_windows_shell_contract.py tests/test_windows_terminal_encoding.py tests/test_terminal_evidence_contract.py tests/test_remote_model_context.py
python -m pytest -q
```

Native Windows and Linux launch tests must both be run on their respective
hosts. Tests alone cannot establish GPU and network portability.

## GitHub: portable payload

Keep the Git repository small. Current `.gitignore` intentionally excludes
`portable/portable-runtimes/`, `portable/portable-models/`,
`portable/portable-slot-cache/`, `.env`, and local manifests that may contain
runtime-specific details.

- Commit source, build scripts, hashes, manifests **without secrets** and docs.
- Distribute pinned runtime binaries as separate verified release artifacts.
- Distribute GGUF weights only when the model license authorizes redistribution;
  preferably provide a download manifest and verified hash.
- Do not push personal SQLite memory/chat databases, API keys, Tailscale state,
  credentials, or KV checkpoint files into a repository or Git LFS.
- Use an independent encrypted backup for long-lived MAI memories and a restore
  procedure. Git alone is not a complete lifetime backup.

## Owner remote-PC session inactivity

An unconnected session keeps its original 600-second setup deadline. Once
the remote agent first polls, a 180-second liveness lease is refreshed by its
polls/results. **A separate ten-hour idle-work clock is NOT refreshed by agent
heartbeats**. It is initially set when the agent connects, then refreshed
when MAI dispatches a remote operation or receives an authenticated result
for an outstanding operation. An authenticated web login alone is not work.

The server checks the deadline on every remote operation/poll and runs a
once-per-minute idle reaper, closing expired sessions and rejecting their
one-time agent credentials. The next remote session must be created and
authorized anew. This revokes MAI's remote command authority; it does **not**
stop the MAI web server, Tailscale Funnel or arbitrary local programs on the
remote PC.

Check with `python -m pytest -q tests/test_remote_idle_timeout.py`.
