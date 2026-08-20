# Using MK5 from another repository

`MK5` can be used as a graph-memory and tool-orchestration engine from another repository, such as `playlist2`.

The important point is that the host repository should own its workspace and memory files. `MK5` should provide the agent pipeline, graph memory, and tools.

## Recommended shape

```text
playlist2/
├── app/
├── data/
├── .machi/
│   ├── memory.db
│   └── sentence_breaker.db
└── playlist_agent.py
```

## Install MACHI for import

From the host repository, install MACHI as an editable dependency.

PowerShell:

```powershell
pip install -e C:\Users\bigla\Documents\Git\MACHI
```

Git Bash:

```bash
pip install -e "C:/Users/bigla/Documents/Git/MACHI"
# or
pip install -e /c/Users/bigla/Documents/Git/MACHI
```

Alternatively, add MACHI as a submodule or vendor directory and make sure the host Python process can import `MK5`.

## Host-specific environment

Set these before creating the `Pipeline`.

```powershell
$env:MK5_WORKSPACE_ROOT="C:\Users\bigla\Documents\Git\playlist2"
$env:MK5_DB_PATH="C:\Users\bigla\Documents\Git\playlist2\.machi\memory.db"
$env:MK5_SENTENCE_BREAKER_DB_PATH="C:\Users\bigla\Documents\Git\playlist2\.machi\sentence_breaker.db"
```

### What each path means

- `MK5_WORKSPACE_ROOT`: the root that `file_search`, `file_create`, `file_read`, `file_update`, `file_delete`, and `terminal_command` use as their starting point.
- `MK5_DB_PATH`: the graph-memory SQLite database for this host project.
- `MK5_SENTENCE_BREAKER_DB_PATH`: the Sentence_Breaker database for this host project.

Keeping these paths inside the host repository gives each project its own memory and file scope.

## Minimal Python usage

```python
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MACHI_DIR = ROOT / ".machi"
MACHI_DIR.mkdir(exist_ok=True)

os.environ.setdefault("MK5_WORKSPACE_ROOT", str(ROOT))
os.environ.setdefault("MK5_DB_PATH", str(MACHI_DIR / "memory.db"))
os.environ.setdefault("MK5_SENTENCE_BREAKER_DB_PATH", str(MACHI_DIR / "sentence_breaker.db"))

from MK5.app.pipeline import Pipeline


async def ask_mk5(user_id: str, message: str):
    pipeline = Pipeline()
    try:
        return await pipeline.run(
            user_id=user_id,
            session_id="playlist2",
            message=message,
        )
    finally:
        pipeline.close()
```

## Tool behavior from a host repo

When `MK5` is embedded into another project, the same tool system is available.

- File discovery should use `file_search`; file work should use `file_create`, `file_read`, `file_update`, and `file_delete`.
- Reading `.txt`, `.md`, or `.markdown` files creates weak, temporary file text activation nodes for the local graph context. They are not fixed as long-term user memory.
- PDF/DOCX files should use `document_read`.
- Images should use `image_analyze`.
- Shell work should use `terminal_command`.
- If the model is unsure about arguments, it can call `tool_manual` for the specific tool instead of guessing.

The model receives the names of model-visible tools, not only file tools. Internal low-level tools remain hidden. A request can still start as a file task and then use `web_research`, terminal inspection, or image/document analysis in the same turn.

## Uploads and remote browsers

For local CLI usage, paths such as `../playlist2/pli_file/tag.txt` are fine.

For browser usage from another PC, path strings can be ambiguous because the browser machine and the server machine may not share the same filesystem. In that case, use the UI attachment button. Uploaded files are stored on the MK5 server under `.mk5_uploads/`, and the resulting server-side path can be passed to tools.

## Model and context settings

Common environment variables:

```powershell
$env:MK5_OLLAMA_MODEL_NAME="gemma4:e4b"
$env:MK5_OLLAMA_IMAGE_MODEL_NAME="gemma4:12b"
$env:MK5_OLLAMA_IMAGE_FALLBACK_MODEL_NAME="gemma4:12b"
$env:MK5_RECENT_MESSAGE_LIMIT="10"
$env:MK5_FILE_TEXT_NODE_KEEP_RATIO="0.7"
$env:MK5_FILE_TEXT_NODE_MAX_ITEMS="24"
$env:MK5_FILE_TEXT_ACTIVATION_MAX_CHARS="8000"
$env:MK5_AGENT_MAX_PARSE_FAILURES="3"
$env:MK5_AGENT_MAX_UNKNOWN_TOOL_GUARDS="2"
```

`MK5_RECENT_MESSAGE_LIMIT` controls how many recent dialogue messages are included in the model input. The default 10 messages correspond to roughly five user/assistant turns. MK5 combines this short window with an activation-weighted graph memory summary.

## Notes

- Create or set the environment variables before importing modules that read `MK5.config`.
- Use a stable `user_id` if the host app wants long-term user memory.
- Use a stable `session_id` when a host app wants short-term conversational continuity.
- `file_search`, `file_create`, `file_read`, `file_update`, and `file_delete` resolve relative paths from `MK5_WORKSPACE_ROOT`, but parent and absolute paths are allowed when the user intentionally works outside the root.
- `terminal_command` starts in `MK5_WORKSPACE_ROOT`, but the command itself may use normal shell navigation such as `..`, absolute paths, or sibling repository paths.
