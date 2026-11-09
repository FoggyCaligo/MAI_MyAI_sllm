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
    assert "remote-requirements.txt" not in paths
    assert "mai/remote/python_worker.py" in paths
    assert "mai/tools/registry.py" in paths
    assert "mai/tools/filesystem.py" in paths
    assert "mai/tools/terminal.py" in paths


def test_powershell_bootstrap_delegates_session_to_persistent_python_client():
    content = Path("mai/app/static/remote-agent.ps1").read_text(encoding="utf-8")
    assert "Initialize-MaiRemotePython" in content
    assert "X-MAI-Bundle-SHA256" in content
    assert "-m mai.remote.agent_client" in content
    assert "-m mai.remote.bootstrap_env" in content
    assert "MAI_REMOTE_ENV_DIGEST" in content
    assert ".shared-envs" in content
    assert "mai.remote.python_worker" not in content
    assert "'file.read' {" not in content
    assert "'file.write' {" not in content
    assert "'process.run' {" not in content


def test_linux_remote_agent_bootstraps_same_python_worker():
    content = Path("mai/app/static/remote-agent-linux.sh").read_text(encoding="utf-8")
    assert "remote/agent/$SESSION/bootstrap" in content
    assert "X-MAI-Bundle-SHA256" in content
    assert "sha256sum" in content
    assert "-m mai.remote.agent_client" in content
    assert "-m mai.remote.bootstrap_env" in content
    assert "MAI_REMOTE_ENV_DIGEST" in content
    assert ".shared-envs" in content
    assert "MAI_REMOTE_SERVER" in content
    assert "MAI_REMOTE_SESSION" in content
    assert "MAI_REMOTE_KEY" in content
    assert "Python 3.11+" in content


def test_remote_agent_scripts_are_packaged():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert '"static/*.ps1"' in pyproject
    assert '"static/*.sh"' in pyproject


def test_worker_bootstraps_require_completed_install_marker():
    windows = Path("mai/app/static/remote-agent.ps1").read_text(encoding="utf-8")
    linux = Path("mai/app/static/remote-agent-linux.sh").read_text(encoding="utf-8")
    assert "'.installed-ok'" in windows
    assert ".shared-env-key" in windows
    assert 'INSTALL_READY="$TARGET/.installed-ok"' in linux
    assert ".shared-env-key" in linux


def test_worker_source_archive_and_native_client_do_not_require_codeact():
    import inspect

    from mai.remote import agent_client
    from mai.remote.python_worker import build_registry

    source = inspect.getsource(agent_client.run_agent)
    assert '"native.invoke"' in source
    assert "execute_code" not in source
    assert not build_registry().has("execute_code")
