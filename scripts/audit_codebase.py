"""Read-only structural source audit; findings are review candidates, not deletion decisions."""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    kind: str
    path: str
    detail: str
    line: int | None = None


def _module_name(root: Path, path: Path) -> str:
    parts = path.relative_to(root).with_suffix("").parts
    return ".".join(parts[:-1] if path.name == "__init__.py" else parts)


def _targets(tree: ast.AST, package: tuple[str, ...]) -> set[str]:
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level > len(package):
                    raise ValueError("relative import crosses package boundary")
                base = package[:len(package) - node.level + 1]
                if node.module:
                    base += tuple(node.module.split("."))
                prefix = ".".join(base)
            else:
                prefix = node.module or ""
            if prefix:
                targets.add(prefix)
            targets.update(f"{prefix}.{alias.name}" for alias in node.names if prefix)
    return targets


def audit(root: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    package_dir = root / "mai"
    if not package_dir.is_dir():
        raise FileNotFoundError(package_dir)
    sources = sorted(package_dir.rglob("*.py"))
    if not sources:
        raise ValueError("no MAI Python modules found")
    auxiliaries = sorted([*root.glob("*.py"), *(root / "scripts").rglob("*.py"),
                          *(root / "tests").rglob("*.py")])
    all_sources = sorted(set(sources + auxiliaries))
    modules = {_module_name(root, path): path for path in sources}
    dependencies: dict[str, set[str]] = {name: set() for name in modules}
    incoming: dict[str, set[str]] = defaultdict(set)
    findings: list[Finding] = []
    fingerprints: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    functions: dict[str, set[str]] = defaultdict(set)
    test_files: set[str] = set()
    for path in all_sources:
        rel = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=rel)
        if path in sources and not source.strip():
            findings.append(Finding("empty_module", rel, "Empty source file"))
        if path in sources and len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant) and isinstance(tree.body[0].value.value, str):
            findings.append(Finding("docstring_only", rel, "Module has no executable declarations"))
        if path.parts and "tests" in path.parts:
            test_files.add(rel)
        local = _module_name(root, path) if path in sources else rel
        package = tuple(_module_name(root, path).split(".")[:-1]) if path.name != "__init__.py" else tuple(_module_name(root, path).split("."))
        for target in _targets(tree, package):
            parts = target.split(".")
            for count in range(len(parts), 0, -1):
                candidate = ".".join(parts[:count])
                if candidate in modules and modules[candidate] != path:
                    incoming[candidate].add(rel)
                    if path in sources:
                        dependencies[local].add(candidate)
                    break
        if path not in sources:
            continue
        lines = source.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            qualified = f"{rel}:{node.lineno}:{node.name}"
            functions[rel].add(node.name)
            length = node.end_lineno - node.lineno + 1 if node.end_lineno else 0
            branches = sum(isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.ExceptHandler, ast.IfExp, ast.Match, ast.BoolOp)) for child in ast.walk(node))
            if length >= 80 or branches >= 20:
                findings.append(Finding("complex_function", rel, f"{node.name}: {length} lines, {branches} branching nodes", node.lineno))
            if length >= 8 and node.end_lineno:
                segment = ast.dump(node, include_attributes=False)
                digest = hashlib.sha256(segment.encode()).hexdigest()
                fingerprints[digest].append((rel, node.lineno, node.name))
    for name, path in modules.items():
        if not incoming[name] and path.name != "__init__.py":
            findings.append(Finding("no_static_inbound_import", path.relative_to(root).as_posix(),
                                    "Check CLI, registration, string references, and dynamic imports"))
    for values in fingerprints.values():
        if len(values) > 1:
            for rel, lineno, name in values:
                findings.append(Finding("identical_function_ast", rel,
                                        f"{name}: same AST as {', '.join(f'{p}:{ln}' for p, ln, _ in values if (p, ln) != (rel, lineno))}", lineno))
    # Tarjan's strongly connected components expose cycles without assuming they are defects.
    index = 0
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: list[list[str]] = []
    def visit(v: str) -> None:
        nonlocal index
        indices[v] = low[v] = index
        index += 1
        stack.append(v)
        on_stack.add(v)
        for w in sorted(dependencies[v]):
            if w not in indices:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], indices[w])
        if low[v] == indices[v]:
            component = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                component.append(w)
                if w == v:
                    break
            if len(component) > 1:
                cycles.append(sorted(component))
    for v in sorted(modules):
        if v not in indices:
            visit(v)
    for cycle in cycles:
        findings.append(Finding("import_cycle", modules[cycle[0]].relative_to(root).as_posix(), " -> ".join(cycle)))
    # Association by AST-import references is a hint, NEVER measured coverage.
    for name, path in modules.items():
        related = sorted(p for p in incoming[name] if p.startswith("tests/"))
        if not related and path.name != "__init__.py":
            findings.append(Finding("no_direct_test_import", path.relative_to(root).as_posix(),
                                    "Does not imply missing runtime coverage"))
    return {"schema_version": 1, "summary": {"modules": len(modules), "python_files_scanned": len(all_sources),
            "test_files_scanned": len(test_files), "findings": len(findings), "import_cycles": len(cycles)},
            "findings": [asdict(f) for f in sorted(findings, key=lambda x: (x.kind, x.path, x.line or 0))],
            "note": "Static review candidates only; no files are removed. No pytest execution or coverage measurement is claimed."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", help="Print structured JSON report")
    args = parser.parse_args()
    result = audit(args.root)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    for finding in result["findings"]:
        print(f'{finding["kind"]}: {finding["path"]}:{finding["line"] or "-"}: {finding["detail"]}')
    print(result["note"])


if __name__ == "__main__":
    main()
