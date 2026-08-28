"""Tailscale Funnel lifecycle for the local MAI web server."""
from __future__ import annotations

import shutil
import subprocess


class TailscaleFunnelError(RuntimeError):
    pass


class TailscaleFunnel:
    """Configure a persistent public Funnel for the MAI local HTTP port."""

    def __init__(self, *, port: int) -> None:
        if not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        self.port = port
        self.status_text: str | None = None

    def start(self) -> str:
        executable = shutil.which("tailscale")
        if executable is None:
            raise TailscaleFunnelError("tailscale executable was not found on PATH")

        configure = subprocess.run(
            [executable, "funnel", "--bg", "--yes", str(self.port)],
            text=True,
            capture_output=True,
            check=False,
        )
        if configure.returncode != 0:
            detail = configure.stderr.strip() or configure.stdout.strip()
            raise TailscaleFunnelError(
                f"tailscale funnel failed with return code {configure.returncode}: {detail}"
            )

        status = subprocess.run(
            [executable, "funnel", "status"],
            text=True,
            capture_output=True,
            check=False,
        )
        if status.returncode != 0:
            detail = status.stderr.strip() or status.stdout.strip()
            raise TailscaleFunnelError(
                f"tailscale funnel status failed with return code {status.returncode}: {detail}"
            )
        status_text = status.stdout.strip()
        if not status_text:
            raise TailscaleFunnelError("tailscale funnel status returned no output")

        self.status_text = status_text
        return status_text

    def stop(self) -> None:
        """Do not disable Funnel on MAI shutdown.

        Funnel is configured with --bg and intentionally persists like the MK4
        launcher. A later MAI start updates the same Funnel target.
        """
        self.status_text = None
