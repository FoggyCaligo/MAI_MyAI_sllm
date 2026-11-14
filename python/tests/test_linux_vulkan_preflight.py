"""Unit contracts for Linux Vulkan preflight; mocked outputs, no host GPU needed."""
from __future__ import annotations

import importlib.util
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "portable" / "check-linux-gpu.py"
spec = importlib.util.spec_from_file_location("mai_linux_gpu_preflight", PATH)
assert spec and spec.loader
gpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gpu)


def test_detects_real_amd_radeon_gpu():
    summary = """GPU0:
\tapiVersion = 1.3
\tdeviceType = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU
\tdeviceName = AMD Radeon RX 6800 XT (RADV NAVI21)
GPU1:
\tdeviceType = PHYSICAL_DEVICE_TYPE_CPU
\tdeviceName = llvmpipe (LLVM 19)
"""
    assert gpu.inspect_vulkan_summary(summary) == [
        ("AMD Radeon RX 6800 XT (RADV NAVI21)", "PHYSICAL_DEVICE_TYPE_DISCRETE_GPU"),
        ("llvmpipe (LLVM 19)", "PHYSICAL_DEVICE_TYPE_CPU"),
    ]
    assert gpu.physical_gpus(gpu.inspect_vulkan_summary(summary)) == [
        ("AMD Radeon RX 6800 XT (RADV NAVI21)", "PHYSICAL_DEVICE_TYPE_DISCRETE_GPU")
    ]


def test_software_only_gpu_is_not_hardware():
    software = [("llvmpipe", "PHYSICAL_DEVICE_TYPE_CPU"),
                ("lavapipe", "PHYSICAL_DEVICE_TYPE_DISCRETE_GPU")]
    assert gpu.physical_gpus(software) == []


def test_integrated_intel_gpu_supported():
    assert gpu.physical_gpus([("Intel(R) Graphics", "PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU")])


def test_unknown_device_without_type_is_not_accepted():
    assert gpu.inspect_vulkan_summary("GPU0:\n    deviceName = unknown\n") == []
