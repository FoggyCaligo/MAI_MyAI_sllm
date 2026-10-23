"""Repository-independent code discovery tools.

These tools deliberately separate code-content discovery from generic file-name
search. They do not infer intent from user text. Search mode, case sensitivity,
file filters, parser choice, and bounds are explicit tool arguments.
"""
from __future__ import annotations

import ast
import importlib
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
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    max_results: int = Field(default=200, ge=1, le=5000)
    max_file_bytes: int = Field(default=2_000_000, ge=1)


class CodeReadInput(_StrictModel):
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    encoding: str = "utf-8"


class CodeEditInput(_StrictModel):
    path: str = Field(min_length=1)
    old_text: str = Field(min_length=1)
    new_text: str
    encoding: str = "utf-8"


class CodeSymbolsInput(_StrictModel):
    path: str
    parser: Literal["python", "javascript", "typescript", "tsx", "c", "cpp", "java", "go", "rust", "ruby"]
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
    start_line: int = 1,
    end_line: int | None = None,
    max_results: int = 200,
    max_file_bytes: int = 2_000_000,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Search source/text contents recursively and return exact line matches.

    Candidate files that cannot be decoded or exceed the explicit size bound are
    ignored rather than copied into the model-facing result. Root/path contract
    failures still raise explicitly.
    """

    if end_line is not None and end_line < start_line:
        raise ValueError("end_line must be >= start_line")

    base = _resolve(root, cwd)
    includes = list(include_globs or [])
    excludes = list(exclude_globs or [])
    flags = 0 if case_sensitive else re.IGNORECASE
    expression = re.compile(query if mode == "regex" else re.escape(query), flags)

    results: list[dict[str, Any]] = []
    truncated = False

    for path, relative in _iter_candidate_files(base, includes, excludes):
        size = path.stat().st_size
        if size > max_file_bytes:
            continue
        try:
            lines = path.read_text(encoding=encoding).splitlines()
        except (UnicodeDecodeError, LookupError):
            continue

        effective_end = len(lines) if end_line is None else min(end_line, len(lines))
        for line_number in range(start_line, effective_end + 1):
            line = lines[line_number - 1]
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
        "start_line": start_line,
        "end_line": end_line,
        "results": results,
        "truncated": truncated,
        "collection": {
            "returned_count": len(results),
            "total_count": None if truncated else len(results),
            "has_more": truncated,
            "complete": not truncated,
        },
    }


def code_read(*, path: str, start_line: int = 1, end_line: int | None = None, encoding: str = "utf-8", cwd: str | Path | None = None) -> dict[str, Any]:
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
    return {"path": str(target), "start_line": start_line, "end_line": min(effective_end, total_lines), "total_lines": total_lines, "lines": selected}


class _PythonSymbolVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.symbols: list[dict[str, Any]] = []
        self._classes: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = ".".join([*self._classes, node.name])
        self.symbols.append({"name": node.name, "qualified_name": qualified, "kind": "class", "line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno)})
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
        self.symbols.append({"name": node.name, "qualified_name": qualified, "kind": kind, "line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno)})



# Explicit language grammars and syntax-node contracts. No file-extension inference.
_TREE_SITTER_GRAMMARS: dict[str, tuple[str, str]] = {
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "c": ("tree_sitter_c", "language"),
    "cpp": ("tree_sitter_cpp", "language"),
    "java": ("tree_sitter_java", "language"),
    "go": ("tree_sitter_go", "language"),
    "rust": ("tree_sitter_rust", "language"),
    "ruby": ("tree_sitter_ruby", "language"),
}

# Keys are grammar-defined node types, not text-matching guesses.
_SYMBOL_NODES: dict[str, dict[str, str]] = {
    "javascript": {
        "class_declaration": "class",
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "method_definition": "method",
    },
    "typescript": {
        "class_declaration": "class",
        "abstract_class_declaration": "class",
        "interface_declaration": "interface",
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "method_definition": "method",
        "method_signature": "method",
    },
    "tsx": {
        "class_declaration": "class",
        "abstract_class_declaration": "class",
        "interface_declaration": "interface",
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "method_definition": "method",
        "method_signature": "method",
    },
    "c": {"function_definition": "function", "struct_specifier": "struct", "enum_specifier": "enum"},
    "cpp": {
        "function_definition": "function",
        "class_specifier": "class",
        "struct_specifier": "struct",
        "namespace_definition": "namespace",
    },
    "java": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
        "method_declaration": "method",
        "constructor_declaration": "constructor",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_spec": "type",
    },
    "rust": {
        "function_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "trait_item": "trait",
        "impl_item": "impl",
        "mod_item": "module",
    },
    "ruby": {"class": "class", "module": "module", "method": "method", "singleton_method": "method"},
}

_SCOPE_KINDS = frozenset({"class", "interface", "enum", "struct", "trait", "module", "namespace", "impl", "type"})


def _tree_sitter_symbols(source: str, language: str, filename: str) -> list[dict[str, Any]]:
    from tree_sitter import Language, Parser

    module_name, factory_name = _TREE_SITTER_GRAMMARS[language]
    grammar = importlib.import_module(module_name)
    parser = Parser(Language(getattr(grammar, factory_name)()))
    encoded = source.encode("utf-8")
    tree = parser.parse(encoded)
    if tree.root_node.has_error:
        raise SyntaxError(f"invalid {language} syntax in {filename}")

    symbols: list[dict[str, Any]] = []
    node_types = _SYMBOL_NODES[language]

    def visit(node: Any, scopes: tuple[str, ...]) -> None:
        kind = node_types.get(node.type)
        next_scopes = scopes
        if kind is not None:
            name_node = node.child_by_field_name("name")
            if name_node is None and node.type == "function_definition" and language in {"c", "cpp"}:
                declarator = node.child_by_field_name("declarator")
                while declarator is not None:
                    nested = declarator.child_by_field_name("declarator")
                    if nested is None:
                        name_node = declarator
                        break
                    declarator = nested
            if name_node is not None:
                name = encoded[name_node.start_byte:name_node.end_byte].decode("utf-8")
                qualified = ".".join((*scopes, name))
                symbols.append({
                    "name": name,
                    "qualified_name": qualified,
                    "kind": "method" if kind == "function" and scopes and language == "rust" else kind,
                    "line": node.start_point.row + 1,
                    "end_line": node.end_point.row + 1,
                })
                if kind in _SCOPE_KINDS:
                    next_scopes = (*scopes, name)

        for child in node.named_children:
            visit(child, next_scopes)

    visit(tree.root_node, ())
    return symbols


def code_symbols(*, path: str, parser: Literal["python", "javascript", "typescript", "tsx", "c", "cpp", "java", "go", "rust", "ruby"], encoding: str = "utf-8", cwd: str | Path | None = None) -> dict[str, Any]:
    """Return parser-derived symbols without regex/name-shape guessing."""

    target = _resolve(path, cwd)
    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_file():
        raise IsADirectoryError(str(target))

    source = target.read_text(encoding=encoding)
    if parser == "python":
        tree = ast.parse(source, filename=str(target))
        visitor = _PythonSymbolVisitor()
        visitor.visit(tree)
        symbols = visitor.symbols
    else:
        symbols = _tree_sitter_symbols(source, parser, str(target))
    return {"path": str(target), "parser": parser, "symbols": symbols}


def code_edit(*, path: str, old_text: str, new_text: str, encoding: str = "utf-8", cwd: str | Path | None = None) -> dict[str, Any]:
    """Replace exactly one existing literal span; fail on absent or ambiguous matches."""
    target = _resolve(path, cwd)
    if not target.is_file():
        if not target.exists():
            raise FileNotFoundError(str(target))
        raise IsADirectoryError(str(target))
    if not old_text:
        raise ValueError("old_text must not be empty")
    with target.open("r", encoding=encoding, newline="") as handle:
        source = handle.read()
    count = source.count(old_text)
    if count != 1:
        raise ValueError(f"code_edit requires exactly one match, found {count}")
    updated = source.replace(old_text, new_text, 1)
    # Existing files are never created by code_edit; file_write owns creation.
    if updated == source:
        raise ValueError("code_edit replacement has no effect")
    with target.open("w", encoding=encoding, newline="") as handle:
        handle.write(updated)
    return {"path": str(target), "replacements": 1, "bytes": target.stat().st_size}


def register_code_edit_tool(registry: ToolRegistry, *, cwd: str | Path | None = None, timeout_seconds: float | None = 60) -> None:
    def handler(**kwargs: Any) -> dict[str, Any]:
        return code_edit(cwd=cwd, **kwargs)

    registry.add(
        name="code_edit",
        description="Replace exactly one occurrence of old_text in an existing local source file with new_text. This is literal, case-sensitive and exact; no fuzzy matching. If absent or repeated, fail without changes. Use code_read/code_search to get the exact old_text first; use file_write to create new files.",
        input_model=CodeEditInput,
        handler=handler,
        timeout_seconds=timeout_seconds,
        category="code",
    )


def register_code_tools(registry: ToolRegistry, *, cwd: str | Path | None = None, timeout_seconds: float | None = 60) -> None:
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
            "Use explicit literal or regex mode, optional include/exclude globs, and optional inclusive start_line/end_line "
            "bounds applied independently to each candidate file. collection.complete=false means more matches may exist beyond the returned results."
        ),
        input_model=CodeSearchInput,
        handler=search_handler,
        timeout_seconds=timeout_seconds,
        category="code",
    )
    registry.add(name="code_read", description="Read an inclusive line range from a local text/code file with stable line numbers.", input_model=CodeReadInput, handler=read_handler, timeout_seconds=timeout_seconds, category="code")
    registry.add(name="code_symbols", description="Parse a source file with an explicit supported parser and return structural symbols. Python uses standard AST; JavaScript, TypeScript, TSX, C, C++, Java, Go, Rust, and Ruby use Tree-sitter grammars. The parser must be selected explicitly.", input_model=CodeSymbolsInput, handler=symbols_handler, timeout_seconds=timeout_seconds, category="code")
