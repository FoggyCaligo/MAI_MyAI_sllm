"""ID-only application access policy.

Authorization is an exact membership check against IDs supplied by configuration.
Authentication identity and memory identity are separate structural concepts.
There is no semantic or pattern-based identity inference.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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
    owner_id: str
    owner_memory_id: str
    trial_ids: frozenset[str]

    def __post_init__(self) -> None:
        owner = self.owner_id.strip()
        if not owner:
            raise ValueError("OWNER_ID must be non-empty")
        if owner != self.owner_id:
            raise ValueError("OWNER_ID must not have surrounding whitespace")

        owner_memory = self.owner_memory_id.strip()
        if not owner_memory:
            raise ValueError("OWNER_MEMORY_ID must be non-empty")
        if owner_memory != self.owner_memory_id:
            raise ValueError("OWNER_MEMORY_ID must not have surrounding whitespace")

        if owner in self.trial_ids:
            raise ValueError("OWNER_ID must not also appear in TRIAL_IDS")
        if owner_memory in self.trial_ids:
            raise ValueError("OWNER_MEMORY_ID must not collide with a trial identity")

        for trial_id in self.trial_ids:
            if not trial_id:
                raise ValueError("TRIAL_IDS must not contain empty IDs")
            if trial_id != trial_id.strip():
                raise ValueError("TRIAL_IDS entries must not have surrounding whitespace")

    @classmethod
    def from_env_values(
        cls,
        *,
        owner_id: str | None,
        owner_memory_id: str | None,
        trial_ids: str | None,
    ) -> "AccessPolicy":
        if owner_id is None:
            raise ValueError("OWNER_ID is required")
        if owner_memory_id is None:
            raise ValueError("OWNER_MEMORY_ID is required")
        trials: list[str] = []
        if trial_ids:
            trials = trial_ids.split(",")
            if len(set(trials)) != len(trials):
                raise ValueError("TRIAL_IDS must not contain duplicate IDs")
        return cls(
            owner_id=owner_id,
            owner_memory_id=owner_memory_id,
            trial_ids=frozenset(trials),
        )

    def authenticate(self, submitted_id: str) -> AccessPrincipal:
        auth_user_id = submitted_id.strip()
        if not auth_user_id:
            raise AccessDeniedError("ID is required")
        if auth_user_id == self.owner_id:
            return AccessPrincipal(
                auth_user_id=auth_user_id,
                memory_user_id=self.owner_memory_id,
                role=AccessRole.OWNER,
            )
        if auth_user_id in self.trial_ids:
            return AccessPrincipal(
                auth_user_id=auth_user_id,
                memory_user_id=auth_user_id,
                role=AccessRole.TRIAL,
            )
        raise AccessDeniedError("ID is not authorized")
