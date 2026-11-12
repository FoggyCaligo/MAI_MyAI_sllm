"""Read physical system RAM capacity; never use momentary available RAM for model tier."""
from __future__ import annotations

import ctypes
import os
import sys


def installed_ram_bytes() -> int:
    if sys.platform == "win32":
        from ctypes import wintypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        state = MemoryStatus()
        state.dwLength = ctypes.sizeof(state)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
            raise OSError(ctypes.get_last_error(), "GlobalMemoryStatusEx failed")
        capacity = int(state.ullTotalPhys)
    elif sys.platform.startswith("linux"):
        capacity = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    else:
        raise RuntimeError(f"Unsupported OS for physical RAM detection: {sys.platform}")
    if capacity <= 0:
        raise RuntimeError("Physical RAM detection returned non-positive capacity")
    return capacity
