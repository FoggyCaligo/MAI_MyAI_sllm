"""Canonical presentation-independent text for decision-verifier claim extraction.

The display Markdown remains unchanged. This representation is built from a
CommonMark parser's tokens, never from character substitutions. Table rows are
expressed with their column names so individual cell values keep their meaning.
"""
from __future__ import annotations

from markdown_it import MarkdownIt
from markdown_it.token import Token


def _inline_text(children: list[Token] | None) -> str:
    parts: list[str] = []
    for token in children or []:
        if token.type in {"text", "code_inline", "html_inline"}:
            parts.append(token.content)
        elif token.type in {"softbreak", "hardbreak"}:
            parts.append("\n")
        elif token.type == "image":
            parts.append(_inline_text(token.children) if token.children else token.content)
    return "".join(parts)


def canonical_verification_text(markdown: str) -> str:
    """Return visible prose and code, preserving table header/value relationships.

    The returned string is the *only* candidate used in extraction span checks
    and as the evidence judge's original answer. Markdown remains the display
    source, not the canonical extraction source.
    """
    if not isinstance(markdown, str):
        raise TypeError("verification candidate must be text")

    tokens = MarkdownIt("default").enable("table").parse(markdown)
    lines: list[str] = []
    in_table = False
    headers: list[str] = []
    row: list[str] = []
    rows: list[list[str]] = []

    for token in tokens:
        if token.type == "table_open":
            in_table = True
            headers, rows = [], []
        elif token.type == "tr_open" and in_table:
            row = []
        elif token.type == "inline":
            value = _inline_text(token.children)
            if in_table:
                row.append(value)
            else:
                lines.append(value)
        elif token.type == "tr_close" and in_table:
            if not headers:
                headers = row
            else:
                rows.append(row)
        elif token.type == "table_close":
            if not headers:
                raise ValueError("parsed Markdown table is missing its header")
            for cells in rows:
                if len(cells) != len(headers):
                    raise ValueError("parsed Markdown table row width differs from header")
                lines.append("; ".join(
                    f"{heading}: {value}" for heading, value in zip(headers, cells, strict=True)
                ))
            in_table = False
        elif token.type in {"fence", "code_block"}:
            lines.append(token.content.rstrip("\n"))

    if in_table:
        raise ValueError("unterminated parsed Markdown table")
    return "\n".join(line for line in lines if line.strip())
