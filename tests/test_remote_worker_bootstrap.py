from pathlib import Path

from mai.app.server import _remote_worker_bundle


def test_remote_worker_bundle_contains_python_registry_and_project_metadata():
    import hashlib
    import io
    import zipfile

    archive_bytes, expected_sha = _remote_worker_bundle()
    assert hashlib.sha256(archive_bytes).hexdigest() == expected_sha
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        paths = set(archive.namelist())
    assert "pyproject.toml" in paths
    assert "mai/remote/python_worker.py" in paths
    assert "mai/tools/registry.py" in paths
    assert "mai/tools/filesystem.py" in paths
    assert "mai/tools/terminal.py" in paths


def test_powershell_delegates_native_tools_to_python_only():
    content = Path("mai/app/static/remote-agent.ps1").read_text(encoding="utf-8")
    assert "Initialize-MaiRemotePython" in content
    assert "X-MAI-Bundle-SHA256" in content
    assert "'native.invoke'" in content
    assert "mai.remote.python_worker" in content
    assert "'file.read' {" not in content
    assert "'file.write' {" not in content
    assert "'process.run' {" not in content
