# Windows signed Python portable runtime

Windows MAI uses **Python Software Foundation-signed CPython** inside
`portable/portable-runtimes/python`. This avoids the unsigned Windows
`python-build-standalone` executable blocked by Smart App Control
(CodeIntegrity 3077 / 3033). Linux/Ubuntu provisioning remains unchanged.

## Commands

- `./mai.sh start`: run existing signed runtime; never install or update Python.
- `./mai.sh check`: verify PSF executable signature, local GGUF catalog and required files; does not start GPU inference.
- `./mai.sh setup`: reuse signed Python 3.11.9, or stage an official signed Python installation in the repository if none exists. Download/pin uv and llama.cpp independently as before. Python packages are installed via portable uv.
- `./mai.sh update`: existing **llama.cpp-only** updater; never updates Python.
- `./mai.sh update-python -PythonVersion 3.11.9`: explicit Python update command. To change versions, specify an **exact 3.11.x patch version** available from the Python install manager. The update is staged and tested before swapping, with the old runtime retained as `python.backup-YYYYMMDD-HHMMSS`. Do not use floating `3.11` tags.

Only `setup` and `update-python` may install Python. They require the **official Python install manager** (`pymanager` or `py`) on the provisioning computer; runtime execution after USB transfer does **not** require that tool. The manager may update *itself* independently of MAI; this does not upgrade MAI's pinned Python runtime. Run the commands while MAI and llama.cpp are stopped.

The default is the previously tested **3.11.9**, not 3.11.18: the latter was distributed in the unsigned Astral build and is not installed by the signed official Windows manager. The older patch release is a deliberate compatibility compromise, not a promise that it contains recent 3.11 security fixes. Future changes to Python versions require explicit user action and compatibility validation.

## Existing installations

A Python 3.11.9 executable that already has a valid PSF signature is adopted by `setup`, with dependencies repaired through portable uv. Its old `.mai-portable-source.json` may still reference Astral; the new `.mai-signed-python.json` clearly marks it as an adopted signed executable, **not** a proof of the original archive's provenance.

If the existing executable is unsigned or uses another version, normal `setup` refuses to overwrite it. On Windows, `./mai.sh setup -PreserveAndReinstallExisting` explicitly keeps the old runtime as a timestamped backup before installing the signed copy. `update-python` also preserves the old runtime and refuses to run while MAI server ports are occupied.

Never swap a lone `python.exe` file; the Python runtime includes DLLs, standard library, and binary dependencies. Never disable Smart App Control to make runtime provisioning pass. Official signatures do not guarantee third-party native extensions will be accepted by every host policy.

## Portability caveats

The executable is located using `$PSScriptRoot` rather than a drive letter. Moving a prepared USB tree to a different Windows computer may still encounter Windows architecture, native DLL, package-path, and App Control restrictions; test the new host. Editable installs may embed absolute paths, so after moving to another computer **rerun setup** with the official manager/portable uv available to regenerate them. Database, GGUF, configuration and user files are never deleted by the Python installer.
