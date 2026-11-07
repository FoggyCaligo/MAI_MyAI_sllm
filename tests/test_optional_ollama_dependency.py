"""Optional Ollama SDK must not be imported by llama.cpp-only MAI startup."""
from __future__ import annotations

import builtins
import importlib
import subprocess
import sys


def test_llamacpp_imports_without_ollama_sdk():
    # This subprocess prevents accidental success because another test has
    # already imported the Ollama package into sys.modules.
    script = r"""
import builtins
real_import = builtins.__import__
def block_ollama(name, *args, **kwargs):
    if name == 'ollama' or name.startswith('ollama.'):
        raise ModuleNotFoundError("No module named 'ollama'", name="ollama")
    return real_import(name, *args, **kwargs)
builtins.__import__ = block_ollama
from mai.llm.llamacpp import LlamaCppAdapter
from mai.app.runtime import MAIRuntime
from mai.agent.verification import FinalGroundingVerifier
from mai.tools.images import ImageAnalyzer
from mai.app.server import app
assert app
print('llamacpp import without ollama SDK: PASS')
"""
    result = subprocess.run([sys.executable, "-c", script], text=True,
                            capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout


def test_ollama_adapter_is_importable_without_ollama_sdk():
    script = r"""
import builtins
real_import = builtins.__import__
def block(name, *args, **kwargs):
    if name == 'ollama' or name.startswith('ollama.'):
        raise ModuleNotFoundError("No module named 'ollama'", name="ollama")
    return real_import(name, *args, **kwargs)
builtins.__import__ = block
from mai.llm.ollama import OllamaAdapter, OllamaRequestError
from mai.llm.models import ModelConfig
class Client:
    async def chat(self, **kwargs):
        return {'message': {'role': 'assistant', 'content': 'ok'}}
adapter = OllamaAdapter(ModelConfig(model='test-model'), client=Client())
assert adapter.config.model == 'test-model'
print('mock Ollama adapter import without SDK: PASS')
"""
    result = subprocess.run([sys.executable, "-c", script], text=True,
                            capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout
