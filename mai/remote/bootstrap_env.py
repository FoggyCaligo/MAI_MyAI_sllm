"""Install reusable remote dependencies independently from MAI source bundles.

This module runs under the host Python before third-party packages are installed.
Only the validated 'remote' dependency group and Python ABI determine the venv
identity. A failed upgrade never destroys the last completed environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tomllib

from .cache import _bundle_lock

_ENV_DIR = ".shared-envs"
_READY = ".installed-ok"
_KEY_FILE = ".shared-env-key"


def dependency_key(pyproject: Path) -> str:
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    groups = config.get("dependency-groups")
    if not isinstance(groups, dict):
        raise ValueError("pyproject.toml must define dependency-groups")
    requirements = groups.get("remote")
    if not isinstance(requirements, list) or not requirements or not all(
        isinstance(requirement, str) and requirement.strip() for requirement in requirements
    ):
        raise ValueError("remote dependency group must be a nonempty list of requirements")
    # Preserve the declared order: installer constraints are part of this contract.
    contract = {
        "requirements": requirements,
        "python": list(sys.version_info[:3]),
        "implementation": sys.implementation.name,
        "platform": sys.platform,
        "machine": platform.machine(),
        "schema": 1,
    }
    return hashlib.sha256(json.dumps(contract, sort_keys=True).encode("utf-8")).hexdigest()


def environment_python(env_dir: Path) -> Path:
    if os.name == "nt":
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


def prepare_environment(base: Path, source: Path) -> Path:
    base = base.resolve(strict=True)
    source = source.resolve(strict=True)
    if source.parent != base:
        raise ValueError("remote source bundle must be a direct child of cache base")
    pyproject = source / "pyproject.toml"
    if not pyproject.is_file():
        raise FileNotFoundError(f"remote dependency declaration missing: {pyproject}")
    key = dependency_key(pyproject)
    env_base = base / _ENV_DIR
    env_base.mkdir(exist_ok=True)
    target = env_base / key

    # Prevent simultaneous installs from replacing a partially written environment.
    # Running clients hold the same per-environment lock while using that Python.
    # The source lock also guards unfinished source bundles against concurrent
    # retention cleanup while a dependency installation is in progress.
    with _bundle_lock(source, nonblocking=False) as source_acquired:
        if not source_acquired:
            raise RuntimeError("remote source bundle lock could not be acquired")
        with _bundle_lock(target, nonblocking=False) as acquired:
            if not acquired:
                raise RuntimeError("shared remote environment lock could not be acquired")
            ready = target / _READY
            executable = environment_python(target)
            if ready.is_file() and executable.is_file():
                print(f"Reusing remote dependency environment: {target}", flush=True)
            else:
                # Do not overwrite a previous complete env until replacement is ready.
                # A partial destination from a prior crash is never interpreted as ready.
                if target.exists():
                    if target.is_symlink() or not target.is_dir():
                        raise RuntimeError(f"invalid shared environment directory: {target}")
                    shutil.rmtree(target)
                try:
                    # Virtualenv console entrypoints encode absolute paths; install
                    # in the final directory instead of renaming a staged venv.
                    print(f"Installing remote dependency environment: {target}", flush=True)
                    subprocess.run([sys.executable, "-m", "venv", str(target)], check=True)
                    python = environment_python(target)
                    subprocess.run([
                        str(python), "-m", "pip", "install",
                        "--disable-pip-version-check", "--no-input", "--upgrade", "pip>=25.1",
                    ], check=True)
                    subprocess.run([
                        str(python), "-m", "pip", "install",
                        "--disable-pip-version-check", "--no-input",
                        "--group", f"{pyproject}:remote",
                    ], check=True)
                    subprocess.run([str(python), "-m", "pip", "check"], check=True)
                    subprocess.run([str(python), "-m", "playwright", "install", "chromium"], check=True)
                    (target / _READY).touch()
                except BaseException:
                    # No completed marker means this environment is not reusable.
                    # A previously completed environment with another key survives.
                    shutil.rmtree(target, ignore_errors=True)
                    raise
        # Publish the exact environment identity only after installation succeeded.
        # Source files are imported through PYTHONPATH, not installed editable into venv.
        (source / _KEY_FILE).write_text(key + "\n", encoding="ascii")
        (source / _READY).touch()
    return executable


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    prepare_environment(args.base, args.source)


if __name__ == "__main__":
    main()
