"""ID-only application access policy.

Authorization is an exact membership check against IDs supplied by configuration.
Authentication identity and memory identity are separate structural concepts.
There is no semantic or pattern-based identity inference.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from typing import Mapping


class AccessRole(str, Enum):
    OWNER = "owner"
    TRIAL = "trial"


class AccessDeniedError(PermissionError):
    """The submitted ID is not authorized to use MAI."""


@dataclass(frozen=True, slots=True)
class AccessPrincipal:
    auth_user_id: str
    memory_user_id: str
    role: AccessRole


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    owner_memory_ids: Mapping[str, str]
    trial_ids: frozenset[str]

    def __post_init__(self) -> None:
        if not self.owner_memory_ids:
            raise ValueError("at least one owner account is required")

        owner_auth_ids = set(self.owner_memory_ids)
        owner_memory_ids = set(self.owner_memory_ids.values())
        if len(owner_memory_ids) != len(self.owner_memory_ids):
            raise ValueError("owner memory identities must be unique")

        for owner_id, memory_id in self.owner_memory_ids.items():
            if not owner_id:
                raise ValueError("owner login IDs must be non-empty")
            if owner_id != owner_id.strip():
                raise ValueError("owner login IDs must not have surrounding whitespace")
            if not memory_id:
                raise ValueError("owner memory IDs must be non-empty")
            if memory_id != memory_id.strip():
                raise ValueError("owner memory IDs must not have surrounding whitespace")

        if owner_auth_ids.intersection(self.trial_ids):
            raise ValueError("owner login IDs must not also appear in TRIAL_IDS")
        if owner_memory_ids.intersection(self.trial_ids):
            raise ValueError("owner memory IDs must not collide with trial identities")

        for trial_id in self.trial_ids:
            if not trial_id:
                raise ValueError("TRIAL_IDS must not contain empty IDs")
            if trial_id != trial_id.strip():
                raise ValueError("TRIAL_IDS entries must not have surrounding whitespace")

    @classmethod
    def from_env_values(
        cls,
        *,
        owner_accounts: str | None = None,
        owner_id: str | None = None,
        owner_memory_id: str | None = None,
        trial_ids: str | None,
    ) -> "AccessPolicy":
        if owner_accounts is not None and (owner_id is not None or owner_memory_id is not None):
            raise ValueError("OWNER_ACCOUNTS cannot be combined with OWNER_ID or OWNER_MEMORY_ID")

        owners: dict[str, str]
        if owner_accounts is not None:
            try:
                decoded = json.loads(owner_accounts)
            except json.JSONDecodeError as exc:
                raise ValueError("OWNER_ACCOUNTS must be a JSON object mapping login IDs to memory IDs") from exc
            if not isinstance(decoded, dict) or not decoded:
                raise ValueError("OWNER_ACCOUNTS must be a non-empty JSON object")
            if not all(isinstance(key, str) and isinstance(value, str) for key, value in decoded.items()):
                raise ValueError("OWNER_ACCOUNTS keys and values must be strings")
            owners = dict(decoded)
        else:
            if owner_id is None:
                raise ValueError("OWNER_ID is required when OWNER_ACCOUNTS is not set")
            if owner_memory_id is None:
                raise ValueError("OWNER_MEMORY_ID is required when OWNER_ACCOUNTS is not set")
            owners = {owner_id: owner_memory_id}

        trials: list[str] = []
        if trial_ids:
            trials = trial_ids.split(",")
            if len(set(trials)) != len(trials):
                raise ValueError("TRIAL_IDS must not contain duplicate IDs")
        return cls(
            owner_memory_ids=owners,
            trial_ids=frozenset(trials),
        )

    def authenticate(self, submitted_id: str) -> AccessPrincipal:
        auth_user_id = submitted_id.strip()
        if not auth_user_id:
            raise AccessDeniedError("ID is required")
        owner_memory_id = self.owner_memory_ids.get(auth_user_id)
        if owner_memory_id is not None:
            return AccessPrincipal(
                auth_user_id=auth_user_id,
                memory_user_id=owner_memory_id,
                role=AccessRole.OWNER,
            )
        if auth_user_id in self.trial_ids:
            return AccessPrincipal(
                auth_user_id=auth_user_id,
                memory_user_id=auth_user_id,
                role=AccessRole.TRIAL,
            )
        raise AccessDeniedError("ID is not authorized")
