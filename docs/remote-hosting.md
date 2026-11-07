# MAI Public Hosting — replaceable adapters

This is the **public web ingress** only, not MAI agent, memory, Ollama,
llama.cpp, SSH, or owner remote-PC command sessions.

Configure one non-secret setting in the project's `settings.yaml`:

```yaml
REMOTE_HOSTING_PROVIDER: none      # none | tailscale | cloudflare
```

Set `tailscale` for the existing Tailscale Funnel path. Set `cloudflare`
for a Cloudflare dashboard-managed named tunnel. Only one provider is
**launched by MAI on startup**; the core HTTP app remains at
`127.0.0.1:8000`. Unknown provider values fail visibly rather than selecting
a fallback. Provider startup failures are logged, but the local MAI server
continues running. Check `/health` for the selected active provider.

### Cloudflare first-time setup

1. Install the official `cloudflared` binary for Windows or Linux and
   make it available on PATH. This is a host OS dependency, not an MAI model.
2. In Cloudflare's dashboard create a **remotely managed named tunnel**.
3. Set its published application route to a domain/subdomain you control,
   targeting `http://127.0.0.1:8000`. Cloudflare manages public HTTPS.
4. Copy only that tunnel's connector token into local, Git-ignored `.env`:

   ```dotenv
   CLOUDFLARE_TUNNEL_TOKEN=your-actual-tunnel-token
   ```

5. Set `REMOTE_HOSTING_PROVIDER: cloudflare` in settings.yaml and restart
   MAI. Verify the tunnel is Healthy in the Cloudflare dashboard, then
   independently test public HTTPS and MAI login. Merely starting cloudflared
   does not prove that the hostname route is live.

The connector receives the token in an environment variable, **never in
command line arguments**. MAI stops the cloudflared subprocess it launched
when MAI exits normally; it does not delete your Cloudflare named tunnel.

### Tailscale first-time setup / switching

Install and authenticate Tailscale on the MAI host; choose
`REMOTE_HOSTING_PROVIDER: tailscale`. The existing Tailscale Funnel
`--bg` configuration is persistent across server restarts **by design**.
Switching provider selection to `cloudflare` or `none` does NOT
automatically disable an already active Tailscale Funnel. Check
`tailscale funnel status` and, when appropriate, disable the old public
Funnel explicitly via `tailscale funnel --https=443 off` or the exact
flags/port used for the original mapping. Check the status again.
Do not automatically erase unrelated host-level Funnel rules.

Legacy `TAILSCALE_FUNNEL` in an old local `.env` is not used to select the
provider. Choose explicitly with `REMOTE_HOSTING_PROVIDER` (an environment
override takes priority over settings.yaml, per MAI's existing settings
contract). The checked-in default is **none**, intentionally fail-closed
against accidental public exposure.

Cloudflare Tunnel published applications normally need a domain managed
in the Cloudflare account; this adapter does **not** offer temporary
trycloudflare URLs or automatic DNS registration.

### Security

Public HTTPS makes MAI's login UI reachable from the Internet.
A MAI owner account can access powerful file/terminal tools: ensure strong
credentials, restrict trial-user tools, use rate limits and preferably add
Cloudflare Access where applicable. Do not add tunnel credentials to
settings.yaml, Git, tests or logs. Public hosting is independent from the
separate **10-hour idle timeout** for owner remote-PC agent sessions.

### Verification

```bash
python -m pytest -q tests/test_hosting_providers.py
python -m pytest -q
```

Both host OSes still require provider binaries and actual login/HTTPS
smoke tests. GitHub code merges alone do not verify live public connectivity.
