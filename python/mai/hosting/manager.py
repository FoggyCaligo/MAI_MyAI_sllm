"""Select precisely one explicitly configured remote hosting provider.

Credentials belong in process environment/.env, never in settings.yaml or logs.
"""
from __future__ import annotations

import os
from typing import Protocol


class HostingError(RuntimeError):
    pass


class HostingProvider(Protocol):
    def start(self) -> str: ...
    def stop(self) -> None: ...


def create_hosting(*, provider: str, port: int) -> HostingProvider | None:
    choice = provider.strip().lower()
    if choice == "none":
        return None
    if not 1 <= port <= 65535:
        raise HostingError("MAI hosting port must be 1..65535")
    if choice == "tailscale":
        from .tailscale import TailscaleProvider
        return TailscaleProvider(port=port)
    if choice == "cloudflare":
        from .cloudflare import CloudflareProvider
        return CloudflareProvider(token=os.environ.get("CLOUDFLARE_TUNNEL_TOKEN", ""))
    raise HostingError(f"Unknown REMOTE_HOSTING_PROVIDER: {choice!r}; expected none/tailscale/cloudflare")
