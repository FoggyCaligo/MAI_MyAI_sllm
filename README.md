# Mai

**English** | [한국어](README.ko.md)

> README maintenance rule: `README.ko.md` is the canonical source. Update the Korean README first, then synchronize this English README to the same structure and level of detail.

Mai is a **semi-GPT implementation project that extends a local sLLM with long-term memory and multiple tools so it can assist with and manage tasks across the user's PC**.

Instead of simply increasing the size of the conversational model, Mai places memory, tool-use, session, and evidence-verification layers around a smaller local model to improve practical usability.

Mai aims to provide an experience where it can:

- continue ordinary conversations naturally,
- remember important information across many conversations,
- understand more of the user's situation, background, preferences, and ongoing work over time,
- answer the same question differently when accumulated personal context makes a more relevant answer possible,
- use real tools for files, documents, images, code, terminals, and web search,
- store memory in local SQLite databases that the user can back up and own directly instead of tying that memory only to an external service account.

In short, Mai is designed as a **personal local AI that does not require the user to repeatedly explain everything to one small model, but instead exposes only the memory and tools that are needed at the moment**.

---

# 1. Overall architecture

```text
User
  ↓
Recent conversation context
  ↓
Mai single agent
  ├─ semantic graph recall
  ├─ scratchpad working memory
  ├─ attachment evidence
  ├─ file / document / image
  ├─ code search
  ├─ terminal
  ├─ web / market
  └─ other work tools
  ↓
Final answer
  ↓
Only model-selected meaning is written to long-term graph memory
```

The graph does not think by itself. One conversational LLM performs reasoning, tool selection, and answer generation. The graph and tools support that LLM by supplying relevant memory and enabling real actions.

---

# 2. Three layers of memory

Mai separates memory by lifetime and purpose.

```text
1. Recent chat / turn memory
   → maintains immediate conversation continuity

2. Scratchpad
   → temporary working memory for the current task

3. Semantic graph memory
   → long-term memory that survives across turns and restarts
```

## 2.1 Recent chat — immediate conversation context

Recent user/assistant messages are stored as raw text in `chat.sqlite3`.

By default, only about the latest 10 messages are injected back into the model. This keeps context size bounded while still supporting natural references such as “what I said earlier.”

Raw chat and long-term graph memory serve different purposes. Mai does not automatically copy every sentence into the graph.

## 2.2 Scratchpad — temporary working memory

Information that is useful only during the current task—such as file contents, attachments, web results, or tool outputs—can be held in the scratchpad.

```text
attachment / tool evidence
        ↓
scratchpad_put
        ↓
scratchpad:1
        ↓
further investigation / work
        ↓
scratchpad_update
```

The scratchpad disappears when the current turn ends.

Only important scratchpad items explicitly selected by the final memory mutation can be used as evidence for durable memory. Unselected scratchpad content is not copied into long-term graph memory.

## 2.3 Semantic graph — personal long-term memory

Long-term memory is stored in `data/graph.sqlite3` as node/edge relationships.

```text
node ─relation→ node
```

Examples:

```text
user ─current project→ Mai
user ─preference→ a working style
Mai ─purpose→ local personal AI
```

Within one user scope, if an exact same canonical node name already exists, Mai reuses the existing node. If the same `(subject, relation, object)` relationship is confirmed again, Mai reinforces the edge by increasing `support_count` instead of duplicating it.

The framework does not merge different strings merely because they appear semantically similar.

---

# 3. Why Mai can understand the user better over time

Mai's long-term memory does not disappear when one conversation ends.

Across multiple days, a user may reveal information such as:

```text
PC environment
ongoing projects
preferred working style
past decisions
recurring requirements
```

Relationships that the model selects as durably meaningful accumulate in the semantic graph.

Later, when a related question appears, Mai does not dump the entire graph into the model. It recalls only the relevant neighborhood. As a result, **the user needs to repeat less background information over time, while Mai gains a stronger basis for answers tailored to that user's actual context.**

This memory is not merely account state stored on a model provider's server. It is kept in local SQLite files that the user can copy and back up directly.

```text
data/graph.sqlite3
```

This file stores Mai's long-term semantic memory and durable source evidence connected to that memory. Restoring the file into the same Mai environment preserves that personal long-term memory.

This makes it possible to treat long-term AI memory as **personal data that the user can own and manage directly**.

Raw conversation history and runtime sessions are stored separately in `chat.sqlite3`. For a complete backup, the simplest approach is to shut Mai down normally and back up the entire `data/` directory.

---

# 4. How long-term memory is created

A turn roughly follows this flow:

```text
current user message
+ recent raw chat
+ existing graph recall when needed
+ attachment evidence
+ current tool results
+ scratchpad when needed
        ↓
      Agent
        ↓
final answer + memory mutation plan
        ↓
Framework validates node / edge / scratchpad / source scope
        ↓
only selected semantic relations are written/revised in the graph
        ↓
selected source evidence is linked to graph provenance
```

The important rule is that **Mai does not automatically pour all raw text into the long-term graph**.

Only semantic relationships selected by the model at the final stage are stored. If a memory is based on scratchpad evidence, it can be linked through the scratchpad to the actual attachment/tool/web evidence that supported it.

---

# 5. How long-term memory is recalled

The model does not receive all of `graph.sqlite3` on every turn.

```text
Current question
  ↓
node_lookup
  ↓
actual candidate node IDs
  ↓
recall_memory
  ↓
1-hop semantic relation
+ compact confidence
+ source_kind
```

Default recall stays compact.

Detailed evidence is opened only when needed:

```text
memory_source_summary
  ↓
source kind / reliability / stability / support / conflict / source ID
  ↓
only when necessary
memory_source_read
  ↓
selected raw evidence range
```

Like `tool_manual` for tools, memory provenance uses **lazy disclosure**.

Confidence is not an arbitrary feeling generated by the model. It compresses structural signals such as:

- baseline reliability for the source kind,
- `support_count` for repeatedly confirmed edges,
- revision/conflict count,
- stability associated with the source kind.

The framework does not inspect sentence content with rules such as `if text contains ...` to decide confidence.

---

# 6. Reducing load on a small sLLM

Mai reduces context pressure at several layers so that a smaller local model is less likely to lose track of instructions under long prompts and large tool schemas.

## 6.1 Tool manuals are lazy-loaded

Mai does not expose every tool's full JSON schema from the beginning.

Initially the model sees only:

```text
tool name + short summary
```

When actual usage details are needed, the model calls:

```text
tool_manual(tool_name)
```

The tool's full description and argument schema then become available.

A tool whose manual has already been read does not remain a manual target again in the same turn.

## 6.2 Tool result compaction

The full runtime event remains available for debugging and execution records, but the copy reinjected into the next model round is compacted.

This helps prevent a single large file or web page from crowding out the rest of the context window.

## 6.3 Only recent context is injected

The normal model input is roughly limited to:

- the current user message,
- the latest 10 raw chat messages,
- the latest 5 compact tool operations,
- the current date,
- graph recall when needed,
- current-turn compact tool history,
- compact tool catalog,
- JSON output contract.

## 6.4 Successful actions are not repeated blindly

If the exact same tool with the exact same JSON arguments has already succeeded in the current turn, Mai prevents the identical side effect from being executed again.

## 6.5 Web grounding

Final answers that rely on web evidence pass through a separate grounding review tied to actual evidence IDs.

The grounding reviewer does not rewrite the answer. It returns only `accept` or `needs_more_evidence`.

---

# 7. Current tool list

## Memory / agent built-ins

| Capability | Purpose |
| --- | --- |
| `node_lookup` | Find candidate nodes in the current user's graph |
| `recall_memory` | Recall actual graph relationships around a candidate node |
| `memory_source_summary` | Inspect compact provenance for recalled nodes/edges |
| `memory_source_read` | Read a bounded raw-evidence range from a source exposed by the summary |
| `tool_manual` | Load the detailed description and JSON schema for a work tool |
| `scratchpad_put` | Create turn-local working memory from current evidence |
| `scratchpad_update` | Update an existing scratchpad item |
| final memory mutation | Write/revise final semantic graph memory |

## File / workspace — owner

| Tool | Purpose |
| --- | --- |
| `file_tree` | Inspect directory structure |
| `file_search` | Search file names and paths |
| `file_text_search` | Search text inside files |
| `file_read` | Read general text files |
| `file_create` | Create a new file |
| `file_update` | Modify an existing file |
| `file_delete` | Delete a file |
| `file_download_link` | Create a temporary browser download link |

Existing-file mutations require concrete path provenance established in the current turn. The final boundary for owner filesystem access is the actual OS/filesystem permission model.

## Document / image — owner work tools

| Tool | Purpose |
| --- | --- |
| `document_read` | Read PDF / DOCX / TXT / MD / MARKDOWN |
| `image_analyze` | Analyze images with the independent vision model configured in `.env` |

## Code — owner

| Tool | Purpose |
| --- | --- |
| `code_index` | Build a compact structural index of a Python repository |
| `code_search` | Search files/symbols through the structural code index |

## Terminal — owner

| Tool | Purpose |
| --- | --- |
| `terminal_command` | Execute a shell command on the current PC |

Terminal output encoding is controlled by `MAI_TERMINAL_ENCODING` in `.env`, not by the model.

## Web / market — owner + trial

| Tool | Purpose |
| --- | --- |
| `latest_search` | Recency-focused public search |
| `web_research` | Search → public page reading → evidence package |
| `market_snapshot` | Market lookup/snapshot through explicitly configured providers |

---

# 8. Owner and Trial accounts

Mai login IDs are assigned either the `owner` or `trial` role.

## Owner

- access to the full work-tool catalog,
- PC filesystem / code / terminal access,
- direct document and image tool calls,
- upload/download,
- multiple persistent sessions allowed.

## Trial

- an independent per-user graph memory,
- core memory capabilities,
- web/market tools,
- **attachment upload and automatic text/document/image analysis for those uploaded attachments**,
- no host filesystem browsing/modification, terminal, or code tools,
- no download-link capability,
- only one active persistent session per trial ID.

Trial uploads are separated by account directory.

```text
.mai_uploads/
├─ friend/
│  └─ ...
└─ family/
   └─ ...
```

A trial account cannot submit an attachment path outside its own upload directory. This preserves the boundary: **a trial user can ask Mai to analyze files they uploaded, but cannot use that capability to browse arbitrary files on the host PC.**

If the same trial ID logs in from another browser, the previous session is revoked.

---

# 9. Work continues even if the browser leaves

`POST /chat` does not hold the HTTP request open until a long model task finishes.

```text
/chat
→ create persistent chat job
→ execute in worker thread
→ browser polls by job ID
```

The server task continues even if the user switches to another app or refreshes the page.

When the UI reconnects, completed messages are restored from `/history` and active work from `/chat/jobs`.

If the server process itself stops, active jobs are not guessed to have succeeded; they remain marked as `interrupted`.

---

# 10. First-time installation — Windows, including non-developers

The steps below assume the PC has no development environment installed yet.

## 10.1 Install Git

Install Git for Windows.

Open a new PowerShell window and verify:

```powershell
git --version
```

If a version is printed, Git is available.

## 10.2 Install Python

Install Python 3. If the installer offers it, enable **Add Python to PATH**.

Open a new PowerShell window and verify:

```powershell
python --version
pip --version
```

## 10.3 Install Ollama

Install and launch Ollama for Windows.

Verify:

```powershell
ollama --version
```

### Minimum model requirement

The **minimum conversational model that has been confirmed to work with Mai is `gemma4:e4b`**.

A model smaller/weaker than `gemma4:e4b` may still generate ordinary text, but can become unreliable at Mai's structured JSON contracts, tool selection, `tool_manual` flow, multi-round context retention, and long-term-memory mutations. For actual use, **`gemma4:e4b` or a stronger model is recommended.**

If Mai behaves strangely after configuring a smaller model, first verify that the selected model meets this minimum capability level.

Download the default example models:

```powershell
ollama pull gemma4:e4b
ollama pull gemma4:12b
```

`gemma4:e4b` is the default conversational-model example and `gemma4:12b` is the default image-analysis-model example. If your hardware allows it, a stronger conversational model can be selected through `MAI_OLLAMA_MODEL` in `.env`.

If the Ollama service is not already running in your environment:

```powershell
ollama serve
```

Run it in a separate terminal.

## 10.4 Clone the Mai repository

Move to the directory where you want the project, then run:

```powershell
git clone https://github.com/FoggyCaligo/MAI_MyAI_sllm.git
cd MAI_MyAI_sllm
```

## 10.5 Create a Python virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks `Activate.ps1`, use a shell/venv activation method that is permitted in your environment rather than silently changing system-wide execution policy.

## 10.6 Install Python packages

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

For development and pytest dependencies:

```powershell
pip install -r requirements-dev.txt
```

## 10.7 Create `.env`

```powershell
Copy-Item .env.example .env
```

Open `.env` in Notepad, VS Code, or another text editor and configure the accounts.

Example:

```dotenv
MAI_OWNER_ID=owner
MAI_ALLOWED_USER_IDS=friend,family

MAI_OLLAMA_MODEL=gemma4:e4b
MAI_OLLAMA_IMAGE_MODEL=gemma4:12b
MAI_HOST=127.0.0.1
MAI_PORT=8000
```

`MAI_OWNER_ID` is the account allowed to use the full PC toolset.

`MAI_ALLOWED_USER_IDS` accepts multiple trial account IDs separated by commas.

```dotenv
MAI_ALLOWED_USER_IDS=friend,family
```

## 10.8 Run Mai

From the project directory with the virtual environment activated:

```powershell
python run_server.py
```

Default address:

```text
http://127.0.0.1:8000/
```

Open it in a browser and log in with an ID configured in `.env`.

## 10.9 External access — optional

If Tailscale is already configured:

```powershell
.\start_public_tailscale.cmd
```

can be used for external HTTPS access.

Publishing the service externally increases the attack surface compared with local-only use, so configure the owner ID and allowed IDs carefully.

---

# 11. Graceful shutdown

The intended shutdown method is from the terminal running Mai:

```text
Ctrl+C
```

```text
Ctrl+C
→ Uvicorn shutdown
→ FastAPI lifespan cleanup
→ SQLite connections close
→ process exits
```

If your Windows terminal does not deliver `Ctrl+C`, `Ctrl+Break` can also be tried.

Closing the terminal window directly is not a graceful shutdown and is not recommended.

SQLite runs in WAL mode, so these files may appear while Mai is running:

```text
graph.sqlite3
graph.sqlite3-wal
graph.sqlite3-shm
chat.sqlite3
chat.sqlite3-wal
chat.sqlite3-shm
```

`-wal` and `-shm` are SQLite runtime companion files, not separate logical databases. Do not manually delete them while Mai is running.

See [`docs/OPERATIONS.md`](docs/OPERATIONS.md) for more details.

---

# 12. Data and backups

## `data/graph.sqlite3`

This is the main file for personal long-term memory.

It contains:

- semantic graph nodes,
- semantic graph edges,
- user anchor,
- support/conflict signals,
- durable graph source evidence,
- graph → source links.

If you want to preserve only the long-term memory layer, shut Mai down normally and back up this file.

## `data/chat.sqlite3`

It contains:

- raw conversation history,
- compact recent tool-operation history,
- authenticated sessions,
- persistent chat jobs.

To preserve the full Mai state, shut Mai down normally and back up the entire `data/` directory.

The database files are created automatically on first run after cloning. Personal memory therefore begins accumulating locally from the first use, and the user can copy those files elsewhere at any time for personal backup and ownership.

---

# 13. Development testing

If development dependencies are installed:

```powershell
python -m pytest -q
```

runs the full contract test suite.

Mai does not hide failures behind fallback behavior merely to make tests pass. When a runtime contract changes, test fixtures should explicitly satisfy the new required contract.

For an ordered live-model regression check after resetting the databases, follow [`docs/MODEL_TEST_GUIDE.md`](docs/MODEL_TEST_GUIDE.md).

---

# 14. Documentation

The repository root keeps only the documents needed to understand the project initially.

- [`README.md`](README.md) — English default README
- [`README.ko.md`](README.ko.md) — canonical Korean README
- [`CONTRACT.md`](CONTRACT.md) — core runtime/product contract
- [`ROADMAP.md`](ROADMAP.md) — remaining development plan

Detailed documents are under [`docs/`](docs/).