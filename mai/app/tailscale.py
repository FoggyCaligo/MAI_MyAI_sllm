"""Tailscale Serve lifecycle for the local MAI web server."""
from __future__ import annotations

import shutil
import subprocess
import time


class TailscaleServeError(RuntimeError):
    pass


class TailscaleServe:
    def __init__(self, *, port: int) -> None:
        if not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        self.port = port
        self._process: subprocess.Popen[str] | None = None
        self.status_text: str | None = None

    def start(self) -> str:
        if self._process is not None:
            raise TailscaleServeError("Tailscale Serve is already running")
        executable = shutil.which("tailscale")
        if executable is None:
            raise TailscaleServeError("tailscale executable was not found on PATH")

        process = subprocess.Popen(
            [executable, "serve", str(self.port)],
            text=True,
        )
        time.sleep(0.35)
        return_code = process.poll()
        if return_code is not None:
            raise TailscaleServeError(
                f"tailscale serve exited during startup with return code {return_code}"
            )

        status = subprocess.run(
            [executable, "serve", "status"],
            text=True,
            capture_output=True,
            check=False,
        )
        if status.returncode != 0:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            detail = status.stderr.strip() or status.stdout.strip()
            raise TailscaleServeError(
                f"tailscale serve status failed with return code {status.returncode}: {detail}"
            )
        status_text = status.stdout.strip()
        if not status_text:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            raise TailscaleServeError("tailscale serve status returned no output")

        self._process = process
        self.status_text = status_text
        return status_text

    def stop(self) -> None:
        process = self._process
        self._process = None
        self.status_text = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
