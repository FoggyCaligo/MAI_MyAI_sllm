"""기존 data 폴더를 독립된 Memory Git 저장소로 안전하게 복사한다.

원본은 삭제하거나 이동하지 않는다. SQLite는 백업 API로 일관된 스냅샷을
생성하며 목적지 파일이 하나라도 이미 있으면 변경하기 전에 실패한다.
운영 중 갱신 누락을 막기 위해 MAI 서버를 종료한 상태에서 실행한다.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sqlite3


def migration_plan(source: Path, target: Path) -> list[tuple[Path, Path]]:
    source = source.resolve(strict=True)
    target = target.resolve(strict=True)
    if not source.is_dir():
        raise NotADirectoryError(source)
    if not target.is_dir():
        raise NotADirectoryError(target)
    if not (target / ".git").exists():
        raise ValueError(f"Memory destination is not a cloned Git repository: {target}")
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError("source and Memory destination must be separate directories")
    files = sorted(
        path for path in source.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(source).parts
        and not path.name.endswith(("-wal", "-shm", "-journal"))
    )
    plan = [(path, target / path.relative_to(source)) for path in files]
    if any(path.is_symlink() for path, _ in plan):
        raise ValueError("old data directory contains symlinked files; resolve before migrating")
    if not plan:
        raise ValueError("old data directory has no files to migrate")
    conflicts = [str(destination) for _, destination in plan if destination.exists()]
    if conflicts:
        raise FileExistsError("Memory repository already contains these files; resolve manually before migrating: " + ", ".join(conflicts))
    return plan


def migrate(plan: list[tuple[Path, Path]]) -> None:
    created: list[Path] = []
    try:
        for source, destination in plan:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise FileExistsError(destination)
            created.append(destination)
            if source.suffix == ".sqlite3":
                with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
                    with sqlite3.connect(destination) as backup:
                        original.backup(backup)
            else:
                shutil.copy2(source, destination)
    except BaseException:
        # Newly created snapshots only. Never touch files that were present
        # before this migration; the originals remain in the old data folder.
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="MAI Memory repository offline data migration")
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--source", type=Path, default=root / "data")
    parser.add_argument("--destination", type=Path, default=root / "mai" / "mcp_tools" / "mcp_memorizer" / "MAI_MEMORY")
    parser.add_argument("--apply", action="store_true", help="copy data after reviewing the plan")
    args = parser.parse_args()
    plan = migration_plan(args.source, args.destination)
    for src, dst in plan:
        print(f"{src} -> {dst}")
    if not args.apply:
        print("Dry run only; use --apply with MAI stopped to copy")
        return
    migrate(plan)
    print("Memory copy completed; source data was not deleted")


if __name__ == "__main__":
    main()
