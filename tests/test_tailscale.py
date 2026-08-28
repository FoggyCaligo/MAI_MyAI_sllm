from __future__ import annotations

from types import SimpleNamespace

import pytest

import mai.app.tailscale as tailscale_module
from mai.app.tailscale import TailscaleServe, TailscaleServeError


class FakeProcess:
    def __init__(self, returncode=None):
        self._returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._returncode

    def terminate(self):
        self.terminated = True
        self._returncode = 0

    def wait(self, timeout=None):
        return self._returncode

    def kill(self):
        self.killed = True
        self._returncode = -9


def test_tailscale_serve_returns_status_text(monkeypatch):
    process = FakeProcess()
    monkeypatch.setattr(tailscale_module.shutil, "which", lambda name: "tailscale")
    monkeypatch.setattr(tailscale_module.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(tailscale_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        tailscale_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="Available within your tailnet:\nhttps://machine.example.ts.net\n\n|-- / proxy http://127.0.0.1:8000\n",
            stderr="",
        ),
    )

    serve = TailscaleServe(port=8000)
    status = serve.start()

    assert "https://machine.example.ts.net" in status
    assert serve.status_text == status
    serve.stop()
    assert process.terminated is True
    assert serve.status_text is None


def test_tailscale_serve_status_failure_is_visible(monkeypatch):
    process = FakeProcess()
    monkeypatch.setattr(tailscale_module.shutil, "which", lambda name: "tailscale")
    monkeypatch.setattr(tailscale_module.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(tailscale_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        tailscale_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="status failed"),
    )

    serve = TailscaleServe(port=8000)
    with pytest.raises(TailscaleServeError, match="status failed"):
        serve.start()
    assert process.terminated is True
