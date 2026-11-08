"""llama.cpp registers no separate image_analyze tool."""
from pathlib import Path

import mai.app.runtime as runtime_module
from mai.app.access import AccessPrincipal, AccessRole


def test_llamacpp_uses_main_model_only_for_vision(monkeypatch):
    for name in (
        "register_memory_tools",
        "register_time_tools",
        "register_calculator_tools",
        "register_external_information_tools",
        "register_browser_tools",
        "register_routed_pc_tools",
        "register_routed_service_tools",
        "register_tool_result_tools",
    ):
        monkeypatch.setattr(runtime_module, name, lambda *args, **kwargs: None)

    instance = object.__new__(runtime_module.MAIRuntime)
    instance.llm_backend = "llamacpp"
    instance.llamacpp_host = "http://localhost:8080"
    instance.cwd = Path(".")
    instance.memory = object()
    instance._llamacpp_router = None

    principal = AccessPrincipal(user_id="owner", db_id="owner", role=AccessRole.OWNER)
    registry = instance._registry_for(principal, object(), object(), object())
    assert not registry.has("image_analyze")
