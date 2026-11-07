"""llama.cpp: main-only vision keeps the Main GGUF resident during agent tool rounds."""
from pathlib import Path

import pytest

import mai.app.runtime as runtime_module
from mai.app.access import AccessPrincipal, AccessRole


@pytest.mark.parametrize("backend,expect_image_tool", [
    ("llamacpp", False),
    ("ollama", True),
])
def test_image_analyze_is_not_registered_in_llamacpp_mode(monkeypatch, backend, expect_image_tool):
    called = []

    for name in (
        "register_memory_tools",
        "register_time_tools",
        "register_calculator_tools",
        "register_external_information_tools",
        "register_browser_tools",
        "register_routed_pc_tools",
        "register_tool_result_tools",
    ):
        monkeypatch.setattr(runtime_module, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runtime_module,
        "register_image_tools",
        lambda *args, **kwargs: called.append(kwargs["model"]),
    )

    # Exercise the real registry composition without initializing storage,
    # external browsers, network services or GPUs.
    instance = object.__new__(runtime_module.MAIRuntime)
    instance.llm_backend = backend
    instance.vision_model = "mai-vision"
    instance.ollama_host = "http://localhost:11434"
    instance.llamacpp_host = "http://localhost:8080"
    instance.cwd = Path(".")
    instance.memory = object()
    instance._llamacpp_router = None

    principal = AccessPrincipal(user_id="owner", db_id="owner", role=AccessRole.OWNER)
    instance._registry_for(principal, object(), object(), object())
    assert bool(called) is expect_image_tool
    assert called == (["mai-vision"] if expect_image_tool else [])
