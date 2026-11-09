"""OpenClaw benchmark configuration regression tests."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GAIA_ADAPTER = ROOT / "benchmark" / "GAIA Benchmark" / "adapter"
LONGMEM_ADAPTER = ROOT / "benchmark" / "LongMemEval Benchmark" / "adapter"
sys.path.insert(0, str(GAIA_ADAPTER))
sys.path.insert(0, str(LONGMEM_ADAPTER))

from run_openclaw_gaia import openclaw_config
from run_openclaw import benchmark_config


class OpenClawBenchmarkConfigTests(unittest.TestCase):
    def test_shared_config_uses_model_context_window_not_agent_context_tokens(self):
        config = openclaw_config("test-model")
        self.assertNotIn("contextTokens", config["agents"]["defaults"])
        model = config["models"]["providers"]["mai-local"]["models"][0]
        self.assertEqual(model["contextWindow"], 131072)
        self.assertEqual(model["maxTokens"], 32768)
        self.assertEqual(config["models"]["providers"]["mai-local"]["timeoutSeconds"], 500)

    def test_longmemeval_plugin_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            plugin = Path(temporary) / "openclaw-plugin"
            plugin.mkdir()
            (plugin / "openclaw.plugin.json").write_text("{}", encoding="utf-8")
            config = benchmark_config("test-model", plugin)
            self.assertEqual(config["plugins"]["load"]["paths"], [str(plugin.resolve())])
            self.assertNotIn("contextTokens", config["agents"]["defaults"])

    def test_longmemeval_requires_real_plugin_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            plugin = Path(temporary) / "missing-plugin"
            with self.assertRaises(NotADirectoryError):
                benchmark_config("test-model", plugin)


if __name__ == "__main__":
    unittest.main()
