# MCP_remote

This is a host-only MCP controller that exposes precisely one model tool:
`remote__switch_workspace(target="local"|"remote")`.

It does **not** install, download or connect to agents through model-visible
tools. Those actions remain part of MAI's authenticated host-controlled remote
worker setup. MAI's common MCP manager executes this stdio server, parses
its requested target, checks ownership and connected worker state, and only
then changes the **current agent run** workspace context.

Each new agent run starts in `local`. Calls already started use a snapshot
of their original target, not whichever location the model selects later.
`MCP_memorizer`, browser/web and host control tools stay on MAI's host.
