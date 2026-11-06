---
name: terminal
description: Execute shell commands on the MAI host or an authorized connected remote computer.
metadata:
  tool-category: terminal
---
# Shell execution

Inspect the activated tool's declared shell family, current working directory, remote execution schema and timeout before constructing commands. Preserve native syntax for the actual target shell: do not nest PowerShell -Command inside PowerShell for no reason. Non-zero exit status may signify no matches for findstr, reg query, or dir and must be interpreted per command contract rather than hidden. An authorized remote machine is targeted with is_remote=true on tools that expose it; omission targets the host, even if the chat user is connected from another device. A missing remote session is a failure, never a reason to silently run on the host.
