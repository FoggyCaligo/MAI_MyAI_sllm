"""Remote public ingress adapter boundary, independent from MAI's agent/runtime."""
from .manager import HostingError, create_hosting

__all__ = ["HostingError", "create_hosting"]
