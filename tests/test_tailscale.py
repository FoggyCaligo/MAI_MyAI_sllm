from __future__ import annotations

from types import SimpleNamespace

import pytest

import mai.app.tailscale as tailscale_module
from mai.app.tailscale import TailscaleFunnel, TailscaleFunnelError


def test_tailscale_funnel_configures_background_public_proxy_and_returns_status(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(tailscale_module.shutil, "which", lambda name: "tailscale")

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[1:3] == ["funnel", "status"]:
            return SimpleNamespace(
                returncode=0,
                stdout="Available on the internet:\nhttps://machine.example.ts.net\n\n|-- / proxy http://127.0.0.1:8000\n",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tailscale_module.subprocess, "run", fake_run)

    funnel = TailscaleFunnel(port=8000)
    status = funnel.start()

    assert calls[0] == ["tailscale", "funnel", "--bg", "--yes", "8000"]
    assert calls[1] == ["tailscale", "funnel", "status"]
    assert "https://machine.example.ts.net" in status
    assert funnel.status_text == status

    funnel.stop()
    assert funnel.status_text is None
    assert len(calls) == 2


def test_tailscale_funnel_configuration_failure_is_visible(monkeypatch):
    monkeypatch.setattr(tailscale_module.shutil, "which", lambda name: "tailscale")
    monkeypatch.setattr(
        tailscale_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="funnel configuration failed",
        ),
    )

    funnel = TailscaleFunnel(port=8000)
    with pytest.raises(TailscaleFunnelError, match="funnel configuration failed"):
        funnel.start()


def test_tailscale_funnel_status_failure_is_visible(monkeypatch):
    calls = 0
    monkeypatch.setattr(tailscale_module.shutil, "which", lambda name: "tailscale")

    def fake_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="status failed")

    monkeypatch.setattr(tailscale_module.subprocess, "run", fake_run)

    funnel = TailscaleFunnel(port=8000)
    with pytest.raises(TailscaleFunnelError, match="status failed"):
        funnel.start()
