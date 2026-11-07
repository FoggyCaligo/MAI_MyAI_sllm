"""Host OS boundary for local/remote MAI terminal execution."""
from .base import HostPlatform
from .linux import LinuxPlatform
from .windows import WindowsPlatform
import os


def current_platform() -> HostPlatform:
    """Resolve at call time: native Windows or Linux; never silently assume Linux."""
    if os.name == "nt":
        return WindowsPlatform()
    if os.name == "posix":
        import sys
        if sys.platform.startswith("linux"):
            return LinuxPlatform()
    raise RuntimeError("MAI terminal supports Windows and Linux hosts only")


__all__ = ["HostPlatform", "LinuxPlatform", "WindowsPlatform", "current_platform"]
