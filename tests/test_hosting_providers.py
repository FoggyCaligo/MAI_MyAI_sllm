"""Public hosting adapters: selection, secrets, process lifecycle and exclusivity."""
from __future__ import annotations

import pytest

from mai.hosting.manager import HostingError, create_hosting
from mai.hosting.cloudflare import CloudflareProvider
from mai.hosting.tailscale import TailscaleProvider


def test_provider_choices(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_TUNNEL_TOKEN", "private-test-token")
    assert create_hosting(provider="none", port=8000) is None
    assert isinstance(create_hosting(provider="tailscale", port=8000), TailscaleProvider)
    assert isinstance(create_hosting(provider="cloudflare", port=8000), CloudflareProvider)
    with pytest.raises(HostingError, match="Unknown"):
        create_hosting(provider="invalid", port=8000)


def test_cloudflare_requires_secret_and_installed_binary(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_TUNNEL_TOKEN", raising=False)
    with pytest.raises(HostingError, match="TOKEN"):
        create_hosting(provider="cloudflare", port=8000).start()
    monkeypatch.setattr("mai.hosting.cloudflare.shutil.which", lambda name: None)
    with pytest.raises(HostingError, match="not found"):
        CloudflareProvider(token="test-token").start()


def test_cloudflare_token_only_in_environment_and_process_stops(monkeypatch):
    seen = {}
    class DummyProcess:
        returncode = None
        def poll(self): return self.returncode
        def terminate(self): seen["terminated"] = True
        def wait(self, timeout): self.returncode = 0
        def kill(self): seen["killed"] = True
    def launch(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return DummyProcess()
    monkeypatch.setattr("mai.hosting.cloudflare.shutil.which", lambda n: "/usr/bin/cloudflared")
    monkeypatch.setattr("mai.hosting.cloudflare.subprocess.Popen", launch)
    provider = CloudflareProvider(token="private-test-token")
    assert "started" in provider.start()
    assert seen["argv"] == ["/usr/bin/cloudflared", "tunnel", "run"]
    assert seen["kwargs"]["env"]["TUNNEL_TOKEN"] == "private-test-token"
    assert "private-test-token" not in str(seen["argv"])
    provider.stop()
    assert seen["terminated"] is True
    assert "killed" not in seen


def test_existing_tailscale_lifecycle_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr("mai.app.tailscale.TailscaleFunnel.start", lambda self: calls.append("start") or "url")
    monkeypatch.setattr("mai.app.tailscale.TailscaleFunnel.stop", lambda self: calls.append("stop"))
    provider = create_hosting(provider="tailscale", port=8000)
    assert provider.start() == "url"
    provider.stop()
    assert calls == ["start", "stop"]
