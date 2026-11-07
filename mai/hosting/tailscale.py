"""Tailscale public Funnel provider; keep existing CLI contract unchanged."""
from __future__ import annotations

from mai.app.tailscale import TailscaleFunnel


class TailscaleProvider:
    def __init__(self, *, port: int) -> None:
        self._funnel = TailscaleFunnel(port=port)

    def start(self) -> str:
        return self._funnel.start()

    def stop(self) -> None:
        # Tailscale Funnel is persistent by existing project design.
        self._funnel.stop()
