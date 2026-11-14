"""Boolean query parser for lexical memory recall.

Precedence: NOT > AND > OR. Operators: AND/OR/NOT or &/|/!.
A query without operators retains the legacy whitespace-split keyword lookup.
"""
from __future__ import annotations

from dataclasses import dataclass

MAX_QUERY_LENGTH = 4096
MAX_TERMS = 64
MAX_DEPTH = 32


@dataclass(frozen=True)
class Expr:
    op: str
    value: str | None = None
    left: "Expr | None" = None
    right: "Expr | None" = None


def _tokens(source: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    i = 0
    while i < len(source):
        ch = source[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "()&|!":
            tokens.append(({"(": "LP", ")": "RP", "&": "AND", "|": "OR", "!": "NOT"}[ch], ch))
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            chars: list[str] = []
            while i < len(source) and source[i] != quote:
                if source[i] == "\\":
                    i += 1
                    if i >= len(source):
                        raise ValueError("unterminated query escape")
                chars.append(source[i])
                i += 1
            if i >= len(source):
                raise ValueError("unterminated quoted query term")
            i += 1
            term = "".join(chars).strip()
            if not term:
                raise ValueError("empty quoted query term")
            tokens.append(("TERM", term))
            continue
        start = i
        while i < len(source) and not source[i].isspace() and source[i] not in "()&|!\"'":
            i += 1
        if i == start:
            raise ValueError("invalid query character")
        word = source[start:i]
        operator = word.upper()
        tokens.append((operator if operator in {"AND", "OR", "NOT"} else "TERM", word))
    return tokens


def parse_query(source: str) -> Expr:
    """Parse Boolean grammar, retaining ordinary multiword keyword behavior."""
    if not isinstance(source, str) or not source.strip():
        raise ValueError("query must be a non-empty string")
    if len(source) > MAX_QUERY_LENGTH:
        raise ValueError("query exceeds maximum length")
    tokens = _tokens(source)
    if not tokens:
        raise ValueError("empty query")
    if not any(kind in {"AND", "OR", "NOT", "LP", "RP"} for kind, _ in tokens):
        # Preserve the old keyword search for ordinary words separated by spaces.
        if len(tokens) == 1:
            return Expr("term", tokens[0][1])
        if any(quote in source for quote in "\"'"):
            raise ValueError("multiple quoted terms require AND or OR")
        return Expr("term", source.strip())
    position = 0
    term_count = 0

    def accept(kind: str) -> bool:
        nonlocal position
        if position < len(tokens) and tokens[position][0] == kind:
            position += 1
            return True
        return False

    def primary(depth: int) -> Expr:
        nonlocal position, term_count
        if depth > MAX_DEPTH:
            raise ValueError("query nesting too deep")
        if accept("LP"):
            item = union(depth + 1)
            if not accept("RP"):
                raise ValueError("missing closing parenthesis")
            return item
        if position < len(tokens) and tokens[position][0] == "TERM":
            term_count += 1
            if term_count > MAX_TERMS:
                raise ValueError("too many query terms")
            term = tokens[position][1]
            position += 1
            return Expr("term", term)
        raise ValueError("expected a query term or opening parenthesis")

    def negate(depth: int) -> Expr:
        if accept("NOT"):
            return Expr("not", left=negate(depth + 1))
        return primary(depth)

    def intersection(depth: int) -> Expr:
        left = negate(depth)
        while accept("AND"):
            left = Expr("and", left=left, right=negate(depth))
        return left

    def union(depth: int) -> Expr:
        left = intersection(depth)
        while accept("OR"):
            left = Expr("or", left=left, right=intersection(depth))
        return left

    expression = union(0)
    if position != len(tokens):
        raise ValueError("unexpected token; use AND or OR between search terms")
    return expression
