"""Credential-based application access policy.

Each configured account has three explicit fields:
- user_id: mutable login identity,
- user_pw: plaintext password supplied from local .env configuration,
- db_id: stable internal identity used for persistent MAI data.

Authorization is exact and structural. There is no semantic or pattern-based
identity inference.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import secrets
from typing import Mapping


class AccessRole(str, Enum):
    OWNER = "owner"
    TRIAL = "trial"


class AccessDeniedError(PermissionError):
    """The submitted credentials are not authorized to use MAI."""


@dataclass(frozen=True, slots=True)
class UserInfo:
    user_id: str
    user_pw: str
    db_id: str


@dataclass(frozen=True, slots=True)
class AccessPrincipal:
    user_id: str
    db_id: str
    role: AccessRole

    # Compatibility aliases for runtime/tool code that still uses the older
    # names. Persistent identity semantics are db_id, not the mutable user_id.
    @property
    def auth_user_id(self) -> str:
        return self.user_id

    @property
    def memory_user_id(self) -> str:
        return self.db_id


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    owners: Mapping[str, UserInfo]
    trials: Mapping[str, UserInfo]

    def __post_init__(self) -> None:
        if not self.owners:
            raise ValueError("OWNER_USERS must contain at least one account")

        all_user_ids = list(self.owners) + list(self.trials)
        if len(set(all_user_ids)) != len(all_user_ids):
            raise ValueError("user_id must be unique across owner and trial accounts")

        all_infos = [*self.owners.values(), *self.trials.values()]
        db_ids = [info.db_id for info in all_infos]
        if len(set(db_ids)) != len(db_ids):
            raise ValueError("db_id must be unique across all accounts")

        for info in all_infos:
            if not info.user_id or info.user_id != info.user_id.strip():
                raise ValueError("user_id must be non-empty and have no surrounding whitespace")
            if not info.user_pw:
                raise ValueError("user_pw must be non-empty")
            if not info.db_id or info.db_id != info.db_id.strip():
                raise ValueError("db_id must be non-empty and have no surrounding whitespace")

    @classmethod
    def from_env_values(
        cls,
        *,
        owner_users: str | None,
        trial_users: str | None,
    ) -> "AccessPolicy":
        owners = _parse_user_infos(owner_users, env_name="OWNER_USERS", required=True)
        trials = _parse_user_infos(trial_users, env_name="TRIAL_USERS", required=False)
        return cls(
            owners={info.user_id: info for info in owners},
            trials={info.user_id: info for info in trials},
        )

    def authenticate(self, submitted_id: str, submitted_password: str) -> AccessPrincipal:
        user_id = submitted_id.strip()
        if not user_id or not submitted_password:
            raise AccessDeniedError("ID or password is incorrect")

        info = self.owners.get(user_id)
        role = AccessRole.OWNER
        if info is None:
            info = self.trials.get(user_id)
            role = AccessRole.TRIAL
        if info is None or not secrets.compare_digest(submitted_password, info.user_pw):
            raise AccessDeniedError("ID or password is incorrect")

        return AccessPrincipal(user_id=info.user_id, db_id=info.db_id, role=role)

    def user_to_db_ids(self) -> dict[str, str]:
        return {
            info.user_id: info.db_id
            for info in [*self.owners.values(), *self.trials.values()]
        }


def _parse_user_infos(raw: str | None, *, env_name: str, required: bool) -> tuple[UserInfo, ...]:
    if raw is None:
        if required:
            raise ValueError(f"{env_name} is required")
        return ()
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{env_name} must be a JSON array of user_info objects") from exc
    if not isinstance(decoded, list):
        raise ValueError(f"{env_name} must be a JSON array")
    if required and not decoded:
        raise ValueError(f"{env_name} must contain at least one account")

    infos: list[UserInfo] = []
    expected_keys = {"user_id", "user_pw", "db_id"}
    for item in decoded:
        if not isinstance(item, dict) or set(item) != expected_keys:
            raise ValueError(
                f"each {env_name} entry must contain exactly user_id, user_pw, and db_id"
            )
        if not all(isinstance(item[key], str) for key in expected_keys):
            raise ValueError(f"all {env_name} user_info fields must be strings")
        infos.append(UserInfo(
            user_id=item["user_id"],
            user_pw=item["user_pw"],
            db_id=item["db_id"],
        ))

    user_ids = [info.user_id for info in infos]
    if len(set(user_ids)) != len(user_ids):
        raise ValueError(f"{env_name} must not contain duplicate user_id values")
    return tuple(infos)
