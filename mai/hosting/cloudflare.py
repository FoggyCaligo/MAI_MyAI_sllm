"""Managed Cloudflare Tunnel connector controlled by MAI server lifecycle.

Configure a dashboard-managed public hostname -> http://localhost:<MAI_PORT>.
Never pass the tunnel secret in argv or print it. No quick ephemeral URL mode.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from .manager import HostingError


class CloudflareProvider:
    def __init__(self, *, token: str) -> None:
        self._token = token.strip()
        self._process: subprocess.Popen | None = None

    def start(self) -> str:
        if not self._token:
            raise HostingError("cloudflare selected but CLOUDFLARE_TUNNEL_TOKEN is missing")
        executable = shutil.which("cloudflared")
        if executable is None:
            raise HostingError("cloudflared not found on PATH; install official connector first")
        if self._process is not None:
            raise HostingError("Cloudflare tunnel already started by MAI")
        env = os.environ.copy()
        env["TUNNEL_TOKEN"] = self._token
        # Logs and command-line arguments must never contain connector secrets.
        try:
            process = subprocess.Popen(
                [executable, "tunnel", "run"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError as exc:
            raise HostingError("cloudflared could not start") from exc
        if process.poll() is not None:
            raise HostingError(f"cloudflared exited during startup (code {process.returncode})")
        self._process = process
        return "Cloudflare Tunnel connector started; confirm route and Healthy state in Cloudflare dashboard"

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
