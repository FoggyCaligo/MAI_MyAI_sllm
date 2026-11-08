"""Guard Windows portable-runtime policy without needing a Windows runner."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_manifest_does_not_resolve_unsigned_python():
    src = (ROOT / "portable/resolve_portable_sources.py").read_text(encoding="utf-8")
    assert '"python": ("astral-sh"' not in src
    assert 'selected = ("uv", "llamacpp")' in src


def test_windows_start_checks_psf_signature_and_no_python_setup():
    script = (ROOT / "mai.ps1").read_text(encoding="utf-8")
    assert "Get-AuthenticodeSignature -LiteralPath $python" in script
    assert "Python Software Foundation" in script
    assert "Install-VerifiedArchive $config.python" not in script
    assert "'update-python'" in script
    assert "install_signed_python.ps1" in script


def test_signed_python_setup_is_staged_and_explicit():
    script = (ROOT / "portable/install_signed_python.ps1").read_text(encoding="utf-8")
    assert "python.incoming" in script
    assert "python.backup-" in script
    assert "Get-AuthenticodeSignature -LiteralPath $exe" in script
    assert "Python Software Foundation" in script
    assert "Install-MaiDependencies $staging" in script
    assert "if ($Mode -eq 'setup' -and $existingValid)" in script
    assert "Test-MaiServerRunning" in script
    # PowerShell 5.1 strips nested quotes in native -c arguments; use --version.\n    assert "& $exe --version" in script\n    assert "print(\\".\\".join" not in script
