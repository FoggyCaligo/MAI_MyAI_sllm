"""Tailscale Funnel lifecycle for the local MAI web server."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


class TailscaleFunnelError(RuntimeError):
    pass


def _find_tailscale_executable() -> str | None:
    """Find Tailscale CLI on PATH or in standard Windows installation directories."""
    discovered = shutil.which("tailscale")
    if discovered:
        return discovered
    if sys.platform != "win32":
        return None
    candidates = [
        Path(os.environ[name]) / "Tailscale" / "tailscale.exe"
        for name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")
        if os.environ.get(name)
    ]
    candidates.append(Path("C:/Program Files/Tailscale/tailscale.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _status_targets_port(status_text: str, port: int) -> bool:
    """Only accept a Funnel already proxying this MAI HTTP port."""
    return bool(re.search(
        rf"\bproxy\s+https?://(?:127\.0\.0\.1|localhost|\[::1\]):{port}(?:/|\s|$)",
        status_text,
        flags=re.IGNORECASE,
    ))


class TailscaleFunnel:
    """Configure a persistent public Funnel for the MAI local HTTP port."""

    def __init__(self, *, port: int) -> None:
        if not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        self.port = port
        self.status_text: str | None = None

    def start(self) -> str:
        executable = _find_tailscale_executable()
        if executable is None:
            raise TailscaleFunnelError(
                "tailscale executable not found on PATH or in standard Windows install locations"
            )

        def read_status() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [executable, "funnel", "status"],
                text=True,
                capture_output=True,
                check=False,
            )

        status = read_status()
        if status.returncode == 0 and _status_targets_port(status.stdout, self.port):
            self.status_text = status.stdout.strip()
            return self.status_text

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

        status = read_status()
        if status.returncode != 0:
            detail = status.stderr.strip() or status.stdout.strip()
            raise TailscaleFunnelError(
                f"tailscale funnel status failed with return code {status.returncode}: {detail}"
            )
        status_text = status.stdout.strip()
        if not status_text:
            raise TailscaleFunnelError("tailscale funnel status returned no output")
        if not _status_targets_port(status_text, self.port):
            raise TailscaleFunnelError(
                f"tailscale funnel is not proxying MAI web port {self.port}: {status_text}"
            )
        self.status_text = status_text
        return status_text

    def stop(self) -> None:
        """Do not disable Funnel on MAI shutdown.

        Funnel is configured with --bg and intentionally persists like the MK4
        launcher. A later MAI start updates the same Funnel target.
        """
        self.status_text = None
