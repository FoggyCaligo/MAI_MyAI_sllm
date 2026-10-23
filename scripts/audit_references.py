"""Report Python source modules that have no inbound static imports.

This is an audit, not a deletion tool. Dynamic imports, CLI entrypoints,
tests, package discovery and file-based references require manual review.
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    sources = sorted((root / "mai").rglob("*.py"))
    modules: dict[str, Path] = {}
    for path in sources:
        relative = path.relative_to(root).with_suffix("")
        module = ".".join(relative.parts)
        if path.name == "__init__.py":
            module = ".".join(relative.parts[:-1])
        modules[module] = path

    incoming: dict[str, set[str]] = defaultdict(set)
    parse_errors: list[str] = []
    for source in sorted((*sources, *root.glob("*.py"), *(root / "scripts").glob("*.py"),
                          *(root / "tests").rglob("*.py"))):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8-sig"), filename=str(source))
        except (OSError, SyntaxError, UnicodeError) as exc:
            parse_errors.append(f"{source.relative_to(root)}: {exc}")
            continue
        path_parts = source.relative_to(root).with_suffix("").parts
        package = list(path_parts[:-1])
        if source.name == "__init__.py":
            package = list(path_parts[:-1])
        for node in ast.walk(tree):
            candidates: list[str] = []
            if isinstance(node, ast.Import):
                candidates = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    if node.level > len(package):
                        continue
                    prefix = package[:len(package) - node.level + 1]
                    if node.module:
                        prefix.extend(node.module.split("."))
                    base = ".".join(prefix)
                else:
                    base = node.module or ""
                candidates = [base, *(f"{base}.{alias.name}" for alias in node.names)]
            for candidate in candidates:
                segments = candidate.split(".")
                for size in range(len(segments), 0, -1):
                    module = ".".join(segments[:size])
                    if module in modules and modules[module] != source:
                        incoming[module].add(str(source.relative_to(root)))
                        break

    print("Modules without inbound static Python imports (review only):")
    for name, path in modules.items():
        if not incoming[name]:
            print(f"  {path.relative_to(root)}")
    print(f"\nAnalyzed {len(sources)} MAI modules. No files were modified.")
    if parse_errors:
        print("\nParse/read failures (audit incomplete):")
        for error in parse_errors:
            print(f"  {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
