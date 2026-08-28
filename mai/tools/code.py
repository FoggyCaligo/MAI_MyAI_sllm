"""Repository-independent code discovery tools.

These tools deliberately separate code-content discovery from generic file-name
search. They do not infer intent from user text. Search mode, case sensitivity,
file filters, parser choice, and bounds are explicit tool arguments.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolRegistry


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodeSearchInput(_StrictModel):
    root: str = "."
    query: str = Field(min_length=1)
    mode: Literal["literal", "regex"] = "literal"
    case_sensitive: bool = False
    include_globs: list[str] = Field(default_factory=list)
    exclude_globs: list[str] = Field(default_factory=list)
    encoding: str = "utf-8"
    max_results: int = Field(default=200, ge=1, le=5000)
    max_file_bytes: int = Field(default=2_000_000, ge=1)


class CodeReadInput(_StrictModel):
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    encoding: str = "utf-8"


class CodeSymbolsInput(_StrictModel):
    path: str
    parser: Literal["python"]
    encoding: str = "utf-8"


def _resolve(path: str, cwd: str | Path | None) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(cwd or os.getcwd()) / candidate
    return candidate.resolve(strict=False)


def _matches_any(path: Path, patterns: list[str]) -> bool:
    return any(path.match(pattern) for pattern in patterns)


def _iter_candidate_files(root: Path, include_globs: list[str], exclude_globs: list[str]):
    if not root.exists():
        raise FileNotFoundError(str(root))
    if root.is_file():
        relative = Path(root.name)
        if include_globs and not _matches_any(relative, include_globs):
            return
        if exclude_globs and _matches_any(relative, exclude_globs):
            return
        yield root, relative
        return
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if include_globs and not _matches_any(relative, include_globs):
            continue
        if exclude_globs and _matches_any(relative, exclude_globs):
            continue
        yield path, relative


def code_search(
    *,
    root: str = ".",
    query: str,
    mode: Literal["literal", "regex"] = "literal",
    case_sensitive: bool = False,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    encoding: str = "utf-8",
    max_results: int = 200,
    max_file_bytes: int = 2_000_000,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Search source/text contents recursively and return exact line matches.

    Files that cannot be decoded or exceed the explicit size bound are reported
    in `skipped`; they are not silently treated as successful searches.
    """

    base = _resolve(root, cwd)
    includes = list(include_globs or [])
    excludes = list(exclude_globs or [])
    flags = 0 if case_sensitive else re.IGNORECASE
    expression = re.compile(query if mode == "regex" else re.escape(query), flags)

    results: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    truncated = False

    for path, relative in _iter_candidate_files(base, includes, excludes):
        size = path.stat().st_size
        if size > max_file_bytes:
            skipped.append({"path": str(path), "reason": f"file exceeds max_file_bytes ({size} > {max_file_bytes})"})
            continue
        try:
            text = path.read_text(encoding=encoding)
        except (UnicodeDecodeError, LookupError) as exc:
            skipped.append({"path": str(path), "reason": f"{type(exc).__name__}: {exc}"})
            continue

        for line_number, line in enumerate(text.splitlines(), start=1):
            match = expression.search(line)
            if match is None:
                continue
            results.append({
                "path": str(path),
                "relative_path": str(relative),
                "line": line_number,
                "column": match.start() + 1,
                "text": line,
            })
            if len(results) >= max_results:
                truncated = True
                break
        if truncated:
            break

    return {
        "root": str(base),
        "query": query,
        "mode": mode,
        "case_sensitive": case_sensitive,
        "results": results,
        "truncated": truncated,
        "skipped": skipped,
    }


def code_read(
    *,
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
    encoding: str = "utf-8",
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Read an exact inclusive line range with stable line numbers."""

    target = _resolve(path, cwd)
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_file():
        raise IsADirectoryError(str(target))

    lines = target.read_text(encoding=encoding).splitlines()
    total_lines = len(lines)
    effective_end = total_lines if end_line is None else end_line
    if effective_end < start_line:
        raise ValueError("end_line must be >= start_line")

    selected = [
        {"line": index, "text": lines[index - 1]}
        for index in range(start_line, min(effective_end, total_lines) + 1)
    ]
    return {
        "path": str(target),
        "start_line": start_line,
        "end_line": min(effective_end, total_lines),
        "total_lines": total_lines,
        "lines": selected,
    }


class _PythonSymbolVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.symbols: list[dict[str, Any]] = []
        self._classes: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = ".".join([*self._classes, node.name])
        self.symbols.append({
            "name": node.name,
            "qualified_name": qualified,
            "kind": "class",
            "line": node.lineno,
            "end_line": getattr(node, "end_lineno", node.lineno),
        })
        self._classes.append(node.name)
        self.generic_visit(node)
        self._classes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._add_function(node, "method" if self._classes else "function")
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        kind = "async_method" if self._classes else "async_function"
        self._add_function(node, kind)
        self.generic_visit(node)

    def _add_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> None:
        qualified = ".".join([*self._classes, node.name])
        self.symbols.append({
            "name": node.name,
            "qualified_name": qualified,
            "kind": kind,
            "line": node.lineno,
            "end_line": getattr(node, "end_lineno", node.lineno),
        })


def code_symbols(
    *,
    path: str,
    parser: Literal["python"],
    encoding: str = "utf-8",
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Return parser-derived symbols without regex/name-shape guessing."""

    target = _resolve(path, cwd)
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_file():
        raise IsADirectoryError(str(target))

    source = target.read_text(encoding=encoding)
    if parser != "python":
        raise ValueError(f"unsupported code symbol parser: {parser}")
    tree = ast.parse(source, filename=str(target))
    visitor = _PythonSymbolVisitor()
    visitor.visit(tree)
    return {"path": str(target), "parser": parser, "symbols": visitor.symbols}


def register_code_tools(
    registry: ToolRegistry,
    *,
    cwd: str | Path | None = None,
    timeout_seconds: float | None = 60,
) -> None:
    def search_handler(**kwargs: Any) -> dict[str, Any]:
        return code_search(cwd=cwd, **kwargs)

    def read_handler(**kwargs: Any) -> dict[str, Any]:
        return code_read(cwd=cwd, **kwargs)

    def symbols_handler(**kwargs: Any) -> dict[str, Any]:
        return code_symbols(cwd=cwd, **kwargs)

    registry.add(
        name="code_search",
        description=(
            "Search file contents recursively from any accessible local path and return exact matching lines. "
            "Use explicit literal or regex mode and optional include/exclude globs."
        ),
        input_model=CodeSearchInput,
        handler=search_handler,
        timeout_seconds=timeout_seconds,
        category="code",
    )
    registry.add(
        name="code_read",
        description="Read an inclusive line range from a local text/code file with stable line numbers.",
        input_model=CodeReadInput,
        handler=read_handler,
        timeout_seconds=timeout_seconds,
        category="code",
    )
    registry.add(
        name="code_symbols",
        description=(
            "Parse a source file with an explicit supported parser and return structural symbols. "
            "Currently supports Python via the standard AST parser; unsupported languages are not guessed."
        ),
        input_model=CodeSymbolsInput,
        handler=symbols_handler,
        timeout_seconds=timeout_seconds,
        category="code",
    )
