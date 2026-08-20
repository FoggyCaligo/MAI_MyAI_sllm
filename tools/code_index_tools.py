from __future__ import annotations

import ast
from pathlib import Path
import re
from typing import Any

from .. import config
from .tool_runtime import ToolDefinition, ToolRegistry


_IGNORED_DIRS = {
    ".git", ".venv", ".uv-cache", ".uv-python", ".pytest_cache", "__pycache__", "node_modules",
}


class CodeIndexToolSuite:
    """Build and query a compact, in-memory structural index of Python code."""

    def __init__(self, workspace_root: Path | None = None) -> None:
        self._workspace_root = (workspace_root or config.WORKSPACE_ROOT).resolve()
        self._indexed_root: Path | None = None
        self._records: list[dict[str, Any]] = []

    def build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="code_index",
                description=(
                    "Build a compact Python repository map without sending full source files to the model. "
                    "Extracts imports, classes, function signatures, routes, tool names, config constants, and tests."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"root": {"type": "string"}},
                    "required": [],
                    "additionalProperties": False,
                },
            ),
            self._index,
        )
        registry.register(
            ToolDefinition(
                name="code_search",
                description=(
                    "Search the compact Python code index and return relevant files and symbols. "
                    "Builds the index automatically if needed; use file_read only for selected source files."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "root": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            self._search,
        )
        return registry

    def _resolve(self, value: str) -> Path:
        raw = Path(value)
        return raw.resolve() if raw.is_absolute() else (self._workspace_root / raw).resolve()

    async def _index(self, arguments: dict[str, Any]) -> dict[str, Any]:
        root_text = str(arguments.get("root") or ".").strip() or "."
        return self._build_index(root_text)

    async def _search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "missing_query", "results": []}
        root_text = str(arguments.get("root") or ".").strip() or "."
        requested_root = self._resolve(root_text)
        if self._indexed_root != requested_root or not self._records:
            built = self._build_index(root_text)
            if built.get("ok") is not True:
                return {**built, "results": []}
        try:
            limit = max(1, min(int(arguments.get("limit", 8)), 20))
        except (TypeError, ValueError):
            limit = 8
        terms = [term.lower() for term in re.findall(r"[\w.-]+", query) if len(term) > 1]
        ranked: list[tuple[float, dict[str, Any], list[str]]] = []
        for record in self._records:
            haystack = str(record.get("_search_text") or "").lower()
            matched = [term for term in terms if term in haystack]
            path = str(record["path"]).lower()
            symbol_text = " ".join([
                *(str(item) for item in record.get("functions", [])),
                *(str(item) for item in record.get("routes", [])),
                *(str(item) for item in record.get("tools", [])),
                *(str(item) for item in record.get("config_constants", [])),
                *(
                    str(cls.get("name") or "") + " " + " ".join(cls.get("methods", []))
                    for cls in record.get("classes", [])
                ),
            ]).lower()
            score = sum(
                5.0 if term in path else 3.0 if term in symbol_text else 1.0
                for term in matched
            )
            score += min(len(record.get("imports", [])), 10) * 0.03
            score += len(record.get("classes", [])) * 0.08
            score += len(record.get("functions", [])) * 0.04
            if "/tests/" in f"/{path}" and not any("test" in term for term in terms):
                score -= 4.0
            if matched or not terms:
                ranked.append((score, record, matched))
        if not ranked:
            ranked = [
                (_centrality(record), record, [])
                for record in self._records
            ]
        ranked.sort(key=lambda item: (-item[0], str(item[1]["path"])))
        return {
            "ok": True,
            "query": query,
            "indexed_root": self._display_path(self._indexed_root or requested_root),
            "results": [
                {**_public_record(record), "matched_terms": matched, "score": round(score, 3)}
                for score, record, matched in ranked[:limit]
            ],
        }

    def _build_index(self, root_text: str) -> dict[str, Any]:
        root = self._resolve(root_text)
        if not root.exists() or not root.is_dir():
            return {
                "ok": False,
                "error": "invalid_root",
                "root": root_text,
                "message": f"Code index root is not a directory: {root_text}",
            }
        records: list[dict[str, Any]] = []
        parse_errors: list[dict[str, str]] = []
        for path in sorted(root.rglob("*.py"), key=lambda item: str(item).lower()):
            if any(part in _IGNORED_DIRS for part in path.parts):
                continue
            try:
                source = path.read_text(encoding="utf-8-sig")
                tree = ast.parse(source, filename=str(path))
                records.append(_analyze_python(path=self._display_path(path), tree=tree))
            except (OSError, SyntaxError, UnicodeError) as exc:
                parse_errors.append({"path": self._display_path(path), "error": str(exc)[:240]})
        self._indexed_root = root
        self._records = records
        packages = sorted({str(Path(record["path"]).parent).replace("\\", "/") for record in records})
        central = sorted(records, key=lambda record: (-_centrality(record), str(record["path"])))[:12]
        return {
            "ok": True,
            "workspace_root": str(self._workspace_root),
            "indexed_root": self._display_path(root),
            "files_indexed": len(records),
            "classes": sum(len(record["classes"]) for record in records),
            "functions": sum(len(record["functions"]) for record in records),
            "routes": sum(len(record["routes"]) for record in records),
            "tools": sorted({name for record in records for name in record["tools"]}),
            "packages": packages[:40],
            "key_files": [record["path"] for record in central],
            "parse_errors": parse_errors[:10],
        }

    def _display_path(self, path: Path) -> str:
        try:
            return path.relative_to(self._workspace_root).as_posix() or "."
        except ValueError:
            return str(path)


def _analyze_python(*, path: str, tree: ast.Module) -> dict[str, Any]:
    _attach_parents(tree)
    imports: list[str] = []
    classes: list[dict[str, Any]] = []
    functions: list[str] = []
    routes: list[str] = []
    tools: list[str] = []
    config_constants: list[str] = []
    tests: list[str] = []
    strings: list[str] = []
    module_doc = (ast.get_docstring(tree) or "").strip()[:300]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level + (node.module or "")
            imports.extend(f"{prefix}.{alias.name}".strip(".") for alias in node.names)
        elif isinstance(node, ast.ClassDef):
            methods = [
                _signature(child)
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            classes.append({"name": node.name, "methods": methods[:30]})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            signature = _signature(node)
            if isinstance(getattr(node, "parent", None), ast.ClassDef):
                continue
            functions.append(signature)
            if node.name.startswith("test_"):
                tests.append(node.name)
            for decorator in node.decorator_list:
                route = _route_name(decorator)
                if route:
                    routes.append(f"{route} -> {node.name}")
        elif isinstance(node, ast.Call) and _call_name(node.func) == "ToolDefinition":
            for keyword in node.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                    tools.append(keyword.value.value)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    config_constants.append(target.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) >= 4:
            strings.append(node.value[:240])
    search_parts = [path, module_doc, *imports, *functions, *routes, *tools, *config_constants, *tests, *strings[:80]]
    search_parts.extend(cls["name"] + " " + " ".join(cls["methods"]) for cls in classes)
    return {
        "path": path,
        "summary": module_doc,
        "imports": sorted(set(imports))[:60],
        "classes": classes[:30],
        "functions": sorted(set(functions))[:60],
        "routes": sorted(set(routes))[:30],
        "tools": sorted(set(tools)),
        "config_constants": sorted(set(config_constants))[:50],
        "tests": sorted(set(tests))[:60],
        "_search_text": "\n".join(search_parts),
    }


def _attach_parents(tree: ast.AST) -> None:
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child.parent = parent  # type: ignore[attr-defined]


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = [arg.arg for arg in [*node.args.posonlyargs, *node.args.args]]
    if node.args.vararg:
        args.append("*" + node.args.vararg.arg)
    args.extend(arg.arg for arg in node.args.kwonlyargs)
    if node.args.kwarg:
        args.append("**" + node.args.kwarg.arg)
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    return f"{prefix}{node.name}({', '.join(args)})"


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _route_name(node: ast.AST) -> str:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return ""
    if node.func.attr.lower() not in {"get", "post", "put", "patch", "delete", "websocket"}:
        return ""
    path = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else "?"
    return f"{node.func.attr.upper()} {path}"


def _centrality(record: dict[str, Any]) -> float:
    score = (
        len(record.get("imports", [])) * 0.2
        + len(record.get("classes", [])) * 1.0
        + len(record.get("functions", [])) * 0.3
        + len(record.get("routes", [])) * 1.5
        + len(record.get("tools", [])) * 1.5
    )
    if "/tests/" in f"/{str(record.get('path') or '').lower()}":
        score -= 5.0
    return score


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}
