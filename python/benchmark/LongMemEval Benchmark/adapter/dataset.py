"""LongMemEval conversation input contract shared across independent agents.

Each question contains its own ordered history. Never provide gold answers,
answer_session_ids or has_answer annotations to an evaluated agent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterator

DATE_FORMAT = "%Y/%m/%d (%a) %H:%M"


@dataclass(frozen=True)
class DialogueTurn:
    role: str
    content: str


@dataclass(frozen=True)
class DialogueSession:
    session_id: str
    date: datetime
    turns: tuple[DialogueTurn, ...]


@dataclass(frozen=True)
class MemoryCase:
    question_id: str
    question_type: str
    question: str
    answer: str
    sessions: tuple[DialogueSession, ...]


def load_cases(path: Path, *, start: int = 0, limit: int = 0) -> Iterator[MemoryCase]:
    if start < 0 or limit < 0:
        raise ValueError("start and limit must be nonnegative")
    if not path.is_file():
        raise FileNotFoundError(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("LongMemEval must contain a nonempty JSON array")
    if start >= len(raw):
        raise ValueError("start must be within the dataset")
    chosen = raw[start:] if limit == 0 else raw[start : start + limit]
    for item in chosen:
        if not isinstance(item, dict):
            raise ValueError("LongMemEval question must be an object")
        qid = item["question_id"]
        qtype = item["question_type"]
        question = item["question"]
        answer = item["answer"]
        if not all(isinstance(x, str) and x for x in (qid, qtype, question)):
            raise ValueError("LongMemEval identity and question must be nonempty strings")
        if not isinstance(answer, str):
            raise TypeError("LongMemEval gold answer must be a string")
        dates = item["haystack_dates"]
        ids = item["haystack_session_ids"]
        sessions = item["haystack_sessions"]
        if not all(isinstance(x, list) for x in (dates, ids, sessions)):
            raise TypeError("LongMemEval session metadata must be arrays")
        if len(dates) != len(ids) or len(ids) != len(sessions):
            raise ValueError("LongMemEval session metadata length mismatch")
        parsed_sessions = []
        for session_id, date, turns in zip(ids, dates, sessions, strict=True):
            if not isinstance(session_id, str) or not session_id:
                raise ValueError("invalid session ID")
            if not isinstance(date, str):
                raise ValueError("invalid session date")
            timestamp = datetime.strptime(date, DATE_FORMAT).replace(tzinfo=timezone.utc)
            if not isinstance(turns, list) or not turns:
                raise ValueError("session must contain dialogue turns")
            parsed_turns = []
            for turn in turns:
                if not isinstance(turn, dict):
                    raise TypeError("LongMemEval turn must be an object")
                role, text = turn["role"], turn["content"]
                if role not in ("user", "assistant") or not isinstance(text, str) or not text:
                    raise ValueError("LongMemEval dialogue turn has invalid role/content")
                parsed_turns.append(DialogueTurn(role, text))
            parsed_sessions.append(DialogueSession(session_id, timestamp, tuple(parsed_turns)))
        if len({s.session_id for s in parsed_sessions}) != len(parsed_sessions):
            raise ValueError("repeated LongMemEval history session ID")
        if any(left.date > right.date for left, right in zip(parsed_sessions, parsed_sessions[1:])):
            raise ValueError("LongMemEval history sessions must be chronological")
        yield MemoryCase(qid, qtype, question, answer, tuple(parsed_sessions))


def dialogue_pairs(session: DialogueSession) -> Iterator[tuple[str, str]]:
    """Original completed user/assistant turns, no generated substitute."""
    if len(session.turns) % 2:
        raise ValueError(f"unpaired dialogue turn in session {session.session_id}")
    for index in range(0, len(session.turns), 2):
        user, assistant = session.turns[index : index + 2]
        if user.role != "user" or assistant.role != "assistant":
            raise ValueError(f"unexpected dialogue roles in session {session.session_id}")
        yield user.content, assistant.content
