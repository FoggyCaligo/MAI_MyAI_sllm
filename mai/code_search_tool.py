from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent import WorkContext, WorkTool
from .file_tools import FileToolAccess, _tool_schema


_IGNORED_DIRS = {
    ".git",
    ".venv",
    ".uv-cache",
    ".uv-python",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
}


@dataclass(slots=True)
class CodeIndexState:
    indexed_root: Path | None = None
    records: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class CodeIndexTool:
    access: FileToolAccess
    state: CodeIndexState
    name: str = "code_index"
    description: str = (
        "Build a compact in-memory structural map of Python source under an explicit root. "
        "Key files returned by the index establish concrete paths for later file_read calls."
    )

    def schema(self) -> dict[str, Any]:
        return _tool_schema(
            self.name,
            {"root": {"type": "string", "minLength": 1}},
            [],
        )

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        self.access.require_owner(context)
        root = self.access.resolve_root(arguments.get("root"))
        if not root.exists():
            raise FileNotFoundError(root)
        if not root.is_dir():
            raise NotADirectoryError(root)

        records: list[dict[str, Any]] = []
        parse_errors: list[dict[str, str]] = []
        for path in sorted(root.rglob("*.py"), key=lambda item: str(item).casefold()):
            if any(part in _IGNORED_DIRS for part in path.parts):
                continue
            try:
                source = path.read_text(encoding="utf-8-sig")
                tree = ast.parse(source, filename=str(path))
                records.append(_analyze_python(path=_display_path(path, root), tree=tree))
            except (OSError, SyntaxError, UnicodeError) as exc:
                parse_errors.append({"path": _display_path(path, root), "error": str(exc)})

        self.state.indexed_root = root
        self.state.records = records
        packages = sorted({str(Path(record["path"]).parent).replace("\\", "/") for record in records})
        central = sorted(records, key=lambda record: (-_centrality(record), str(record["path"])))[:12]
        return {
            "indexed_root": str(root),
            "files_indexed": len(records),
            "classes": sum(len(record["classes"]) for record in records),
            "functions": sum(len(record["functions"]) for record in records),
            "routes": sum(len(record["routes"]) for record in records),
            "tools": sorted({name for record in records for name in record["tools"]}),
            "packages": packages[:40],
            "key_files": [record["path"] for record in central],
            "parse_errors": parse_errors,
        }

    @staticmethod
    def discovered_paths(result: dict[str, Any]) -> set[str]:
        root = Path(str(result["indexed_root"]))
        return {str((root / str(path)).resolve()) for path in result.get("key_files", [])}


@dataclass(slots=True)
class CodeSearchTool:
    access: FileToolAccess
    state: CodeIndexState
    index_tool: CodeIndexTool
    name: str = "code_search"
    description: str = (
        "Search the current in-memory structural Python code index and return relevant files and symbols. "
        "Returned result files establish concrete paths for later file_read calls."
    )

    def schema(self) -> dict[str, Any]:
        return _tool_schema(
            self.name,
            {
                "query": {"type": "string", "minLength": 1},
                "root": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            ["query"],
        )

    def execute(self, *, arguments: dict[str, Any], context: WorkContext) -> dict[str, Any]:
        self.access.require_owner(context)
        query = str(arguments["query"]).strip()
        if not query:
            raise ValueError("code_search query must be non-empty")
        root = self.access.resolve_root(arguments.get("root"))
        if self.state.indexed_root != root or not self.state.records:
            self.index_tool.execute(arguments={"root": str(root)}, context=context)

        limit = int(arguments.get("limit", 8))
        terms = [term.casefold() for term in re.findall(r"[\w.-]+", query) if len(term) > 1]
        ranked: list[tuple[float, dict[str, Any], list[str]]] = []
        for record in self.state.records:
            haystack = str(record.get("_search_text") or "").casefold()
            matched = [term for term in terms if term in haystack]
            path_text = str(record["path"]).casefold()
            symbol_text = " ".join(
                [
                    *(str(item) for item in record.get("functions", [])),
                    *(str(item) for item in record.get("routes", [])),
                    *(str(item) for item in record.get("tools", [])),
                    *(str(item) for item in record.get("config_constants", [])),
                    *(
                        str(cls.get("name") or "") + " " + " ".join(cls.get("methods", []))
                        for cls in record.get("classes", [])
                    ),
                ]
            ).casefold()
            score = sum(
                5.0 if term in path_text else 3.0 if term in symbol_text else 1.0
                for term in matched
            )
            score += min(len(record.get("imports", [])), 10) * 0.03
            score += len(record.get("classes", [])) * 0.08
            score += len(record.get("functions", [])) * 0.04
            if matched:
                ranked.append((score, record, matched))

        ranked.sort(key=lambda item: (-item[0], str(item[1]["path"])))
        return {
            "query": query,
            "indexed_root": str(self.state.indexed_root or root),
            "results": [
                {**_public_record(record), "matched_terms": matched, "score": round(score, 3)}
                for score, record, matched in ranked[:limit]
            ],
        }

    @staticmethod
    def discovered_paths(result: dict[str, Any]) -> set[str]:
        root = Path(str(result["indexed_root"]))
        return {str((root / str(item["path"])).resolve()) for item in result.get("results", []) if item.get("path")}


def build_code_tools(*, owner_id: str, default_root: Path | None = None) -> list[WorkTool]:
    access = FileToolAccess(owner_id=owner_id, default_root=(default_root or Path.cwd()).resolve())
    state = CodeIndexState()
    index = CodeIndexTool(access=access, state=state)
    search = CodeSearchTool(access=access, state=state, index_tool=index)
    return [index, search]


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
        elif isinstance(node, ast.Call) and _call_name(node.func) in {"ToolDefinition", "FunctionWorkTool"}:
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
    if node.func.attr.casefold() not in {"get", "post", "put", "patch", "delete", "websocket"}:
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
    if "/tests/" in f"/{str(record.get('path') or '').casefold()}":
        score -= 5.0
    return score


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix() or "."
    except ValueError:
        return str(path)
