#!/usr/bin/env python3
"""Inspect Vulkan GPU availability on an Ubuntu host without changing drivers.

This checks the Vulkan loader's reported physical devices, not a successful
llama.cpp/Ollama model inference. Never silently treat lavapipe as a real GPU.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys


def inspect_vulkan_summary(output: str) -> list[tuple[str, str]]:
    """Return (deviceName, deviceType) pairs from vulkaninfo --summary output."""
    devices: list[tuple[str, str]] = []
    name = ""
    kind = ""
    in_device = False

    def flush() -> None:
        if name and kind:
            devices.append((name, kind))

    for line in output.splitlines():
        if re.match(r"^GPU\d+:\s*$", line.strip()):
            if in_device:
                flush()
            in_device, name, kind = True, "", ""
        elif in_device:
            match_name = re.match(r"\s*deviceName\s*=\s*(.+)", line)
            match_type = re.match(r"\s*deviceType\s*=\s*(.+)", line)
            if match_name:
                name = match_name.group(1).strip()
            if match_type:
                kind = match_type.group(1).strip()
    if in_device:
        flush()
    return devices


def physical_gpus(devices: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [
        (name, kind) for name, kind in devices
        if kind in {"PHYSICAL_DEVICE_TYPE_DISCRETE_GPU", "PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU"}
        and not re.search(r"llvmpipe|lavapipe|software rasterizer|swiftshader", name, re.IGNORECASE)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require", action="store_true", help="exit nonzero if no hardware Vulkan GPU")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        print("[MAI GPU] Linux host required for Vulkan preflight", file=sys.stderr)
        return 2
    if shutil.which("vulkaninfo") is None:
        print("[MAI GPU] vulkaninfo not found. Ubuntu: sudo apt install libvulkan1 mesa-vulkan-drivers vulkan-tools", file=sys.stderr)
        return 1 if args.require else 0
    result = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True, check=False, timeout=30)
    if result.returncode:
        print("[MAI GPU] vulkaninfo failed; check GPU driver, Vulkan ICD and /dev/dri/renderD* permissions", file=sys.stderr)
        print((result.stderr or result.stdout)[-1500:], file=sys.stderr)
        return 1 if args.require else 0
    devices = inspect_vulkan_summary(result.stdout)
    for name, kind in devices:
        print(f"[MAI GPU] Vulkan device: {name} ({kind})")
    hardware = physical_gpus(devices)
    if not hardware:
        print("[MAI GPU] No hardware Vulkan GPU detected (CPU software Vulkan is not accepted)", file=sys.stderr)
        return 1 if args.require else 0
    print(f"[MAI GPU] {len(hardware)} hardware Vulkan device(s) detected; model inference still needs a runtime smoke test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
