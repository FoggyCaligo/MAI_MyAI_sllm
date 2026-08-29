"""Per-principal upload directory helpers."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .access import AccessPrincipal, AccessRole


def trial_upload_directory(upload_root: str | Path, trial_id: str) -> Path:
    """Return a stable, path-safe private upload directory for one trial ID."""
    clean_trial_id = trial_id.strip()
    if not clean_trial_id:
        raise ValueError("trial_id must be non-empty")
    digest = hashlib.sha256(clean_trial_id.encode("utf-8")).hexdigest()[:24]
    return Path(upload_root).expanduser().resolve(strict=False) / "trials" / digest


def principal_upload_directory(upload_root: str | Path, principal: AccessPrincipal) -> Path:
    """Resolve the upload directory exposed to a principal.

    Owner keeps the historical root directory. Trial accounts receive stable,
    mutually separate subdirectories under ``trials/``.
    """
    root = Path(upload_root).expanduser().resolve(strict=False)
    if principal.role is AccessRole.OWNER:
        return root
    if principal.role is AccessRole.TRIAL:
        return trial_upload_directory(root, principal.auth_user_id)
    raise ValueError(f"unsupported access role: {principal.role!r}")
