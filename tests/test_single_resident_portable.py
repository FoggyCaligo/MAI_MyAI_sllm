"""Single-resident Portable router contract across supported operating systems."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_llamacpp_router_allows_one_resident_model_only():
    ps1 = (ROOT / "portable/start-portable-llamacpp-router.ps1").read_text(encoding="utf-8")
    assert '[ValidateRange(1,1)][int]$ModelsMax = 1' in ps1
    assert "'--models-max', \"$ModelsMax\"" in ps1


def test_linux_llamacpp_router_allows_one_resident_model_only():
    sh = (ROOT / "portable/start-portable-llamacpp-linux.sh").read_text(encoding="utf-8")
    assert 'models_max="${1:-1}"' in sh
    assert '"$models_max" == 1' in sh
    assert '--models-max "$models_max"' in sh


def test_settings_uses_ornith_main_and_matching_projector():
    content = (ROOT / "settings.yaml").read_text(encoding="utf-8")
    assert 'LLAMACPP_MAIN_GGUF_PATH: "portable/portable-models/gguf/Ornith-1.5-9B-Q4_K_M.gguf"' in content
    assert 'LLAMACPP_MAIN_MMPROJ_PATH: "portable/portable-models/gguf/mmproj-Ornith-1.5-9B-BF16.gguf"' in content
    assert 'LLAMACPP_GGUF_DIRECTORY: "portable/portable-models/gguf"' in content

def test_windows_and_linux_router_default_to_128k_context():
    win = (ROOT / "portable/start-portable-llamacpp-router.ps1").read_text(encoding="utf-8")
    linux = (ROOT / "portable/start-portable-llamacpp-linux.sh").read_text(encoding="utf-8")
    assert "[int]$ContextSize = 131072" in win
    assert '"$root/portable/llamacpp_model_catalog.py"' in linux
    catalog = (ROOT / "portable/llamacpp_model_catalog.py").read_text(encoding="utf-8")
    assert 'default=131072' in catalog
