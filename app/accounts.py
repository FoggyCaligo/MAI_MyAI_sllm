from __future__ import annotations

import hashlib
import hmac
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from .. import config


@dataclass(frozen=True, slots=True)
class Account:
    role: str
    graph_user_id: str


class AccountStore:
    """Exact-match login allowlist sourced from MK5 environment settings."""

    def __init__(
        self,
        *,
        allowed_login_ids: Iterable[str] | None = None,
        owner_login_id: str | None = None,
        owner_graph_user_id: str | None = None,
    ) -> None:
        source = config.ALLOWED_LOGIN_IDS if allowed_login_ids is None else allowed_login_ids
        self._allowed_login_ids = tuple(
            dict.fromkeys(_normalize_login_id(str(item)) for item in source if str(item).strip())
        )
        self._owner_login_id = _normalize_login_id(
            config.OWNER_LOGIN_ID if owner_login_id is None else owner_login_id
        )
        self._owner_graph_user_id = (
            config.OWNER_GRAPH_USER_ID if owner_graph_user_id is None else owner_graph_user_id
        ).strip() or "account::owner"

    def authenticate(self, login_id: str) -> Account | None:
        candidate = _normalize_login_id(login_id)
        if not candidate or candidate == "default-user":
            return None
        if not any(_constant_time_text_equal(candidate, allowed) for allowed in self._allowed_login_ids):
            return None
        is_owner = bool(self._owner_login_id) and _constant_time_text_equal(
            candidate, self._owner_login_id
        )
        if is_owner:
            return Account(role="owner", graph_user_id=self._owner_graph_user_id)
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:24]
        return Account(role="trial", graph_user_id=f"account::trial::{digest}")

    def list_accounts(self) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for login_id in self._allowed_login_ids:
            account = self.authenticate(login_id)
            if account is not None:
                items.append({"role": account.role, "graph_user_id": account.graph_user_id})
        return items

    def is_active(self, account: Account) -> bool:
        return any(self.authenticate(login_id) == account for login_id in self._allowed_login_ids)


def _constant_time_text_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _normalize_login_id(value: str) -> str:
    return unicodedata.normalize("NFC", value.strip())
