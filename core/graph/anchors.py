from __future__ import annotations

import hashlib


ASSISTANT_ANCHOR_ID = "assistant_anchor::global"
SEARCH_ANCHOR_ID = "search_anchor::global"


def user_anchor_id(user_id: str) -> str:
    normalized = user_id.strip()
    if not normalized:
        raise ValueError("user_id must not be empty")
    return f"user_anchor::{normalized}"


def utterance_node_id(user_id: str, text: str, session_id: str | None = None) -> str:
    base = f"{user_id}|{session_id or ''}|{text}"
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()
    return f"utterance::{digest}"


def fact_node_id(owner_id: str, fact_text: str, *, namespace: str = "fact") -> str:
    base = f"{owner_id}|{namespace}|{fact_text}"
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()
    return f"{namespace}::{digest}"


def search_query_node_id(query: str) -> str:
    digest = hashlib.sha256(query.strip().encode("utf-8")).hexdigest()
    return f"search_query::{digest}"


def search_result_node_id(query: str, title: str, url: str) -> str:
    base = f"{query}|{title}|{url}"
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()
    return f"search_result::{digest}"


def concept_node_id(surface: str) -> str:
    digest = hashlib.sha256(surface.strip().lower().encode("utf-8")).hexdigest()
    return f"concept::{digest}"
