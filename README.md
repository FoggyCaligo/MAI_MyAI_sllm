# Mai

**English** | [한국어](README.ko.md)

> README maintenance rule: `README.ko.md` is the canonical source. Update the Korean README first, then synchronize this English README to the same structure and level of detail.

Mai is a **semi-GPT implementation project that extends a local sLLM with long-term memory and multiple tools so it can assist with and manage tasks across the user's PC**.

Instead of simply increasing the size of the conversational model, Mai places memory, tool-use, session, and evidence-verification layers around a smaller local model to improve practical usability.

Mai aims to provide an experience where it can:

- continue ordinary conversations naturally,
- remember important information across many conversations,
- understand more of the user's situation, background, preferences, and ongoing work over time,
- tailor answers to accumulated personal context,
- use real tools for files, documents, images, code, terminals, and web search,
- store memory in local SQLite databases that the user can back up and own directly.

In short, Mai is designed as a **personal local AI that does not require the user to repeatedly explain everything to one small model, but instead exposes only the memory and tools needed at the moment**.

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
   → immediate conversation continuity

2. Scratchpad
   → temporary working memory for the current task

3. Semantic graph memory
   → long-term memory across turns and restarts
```

## 2.1 Recent chat

Recent user/assistant messages are stored as raw text in `chat.sqlite3`.

By default, only about the latest 10 messages are injected back into the model. This keeps context bounded while still supporting natural references such as “what I said earlier.”

Raw chat and long-term graph memory serve different purposes. Mai does not automatically copy every sentence into the graph.

## 2.2 Scratchpad

Information useful only during the current task—file contents, attachments, web results, or tool outputs—can be held in the scratchpad.

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

The scratchpad disappears when the turn ends. Only scratchpad items selected by the final memory mutation can be used as durable-memory evidence.

## 2.3 Semantic graph

Long-term memory is stored in `data/graph.sqlite3` as node/edge relationships.

```text
node ─relation→ node
```

Within one user scope, if an exact same canonical node name already exists, Mai reuses it. If the same `(subject, relation, object)` relationship is confirmed again, Mai increases `support_count` instead of duplicating the edge.

The framework does not merge different strings merely because they appear semantically similar.

---

# 3. Why Mai can understand the user better over time

Mai's long-term memory survives individual conversations.

Information such as the user's PC environment, ongoing projects, preferences, past decisions, and recurring requirements can accumulate as semantic relationships when the model selects them as durably meaningful.

Later, Mai recalls only the relevant graph neighborhood instead of dumping the whole graph into the model. As a result, **the user needs to repeat less background information over time, while Mai gains a stronger basis for answers tailored to that user's actual context.**

This memory is stored locally:

```text
data/graph.sqlite3
```

The file contains long-term semantic memory and durable source evidence linked to that memory. Because it can be copied and backed up directly, long-term AI memory can be treated as **personal data owned and managed by the user**.

Raw conversation history and sessions are stored separately in `data/chat.sqlite3`. For a complete backup, shut Mai down normally and back up the entire `data/` directory.

---

# 4. How long-term memory is created and recalled

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
only selected semantic relations are written/revised
        ↓
selected source evidence is linked to graph provenance
```

Mai does not automatically pour all raw text into the long-term graph.

Recall is also compact:

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

Detailed evidence is opened only when needed:

```text
memory_source_summary
  ↓
memory_source_read
  ↓
selected raw evidence range
```

Confidence compresses structural signals such as source-kind reliability, `support_count`, revision/conflict count, and stability. The framework does not inspect sentence contents with string heuristics to invent confidence.

---

# 5. Reducing load on a small sLLM

Mai reduces context pressure at several layers.

## 5.1 Lazy tool manuals

The model initially sees only:

```text
tool name + short summary
```

When detailed usage is needed, it calls:

```text
tool_manual(tool_name)
```

and only that tool's full schema is exposed.

## 5.2 Tool-result compaction

Full runtime events remain available for execution/debugging records, while the copy reinjected into the next model round is compacted.

## 5.3 Bounded recent context

Normal model input is roughly limited to:

- current user message,
- latest 10 raw chat messages,
- latest 5 compact tool operations,
- current date,
- graph recall when needed,
- current-turn compact tool history,
- compact tool catalog,
- JSON output contract.

## 5.4 Successful-action deduplication

If the exact same tool with the exact same JSON arguments has already succeeded in the current turn, the same side effect is not blindly executed again.

## 5.5 Web grounding

Final answers based on web evidence are checked against actual evidence IDs. The grounding reviewer returns only `accept` or `needs_more_evidence`; it does not rewrite the final answer.

---

# 6. Current tool list

## Memory / agent built-ins

| Capability | Purpose |
| --- | --- |
| `node_lookup` | Find candidate nodes in the current user's graph |
| `recall_memory` | Recall graph relationships around a candidate node |
| `memory_source_summary` | Inspect compact provenance for recalled nodes/edges |
| `memory_source_read` | Read a bounded raw-evidence range from a selected source |
| `tool_manual` | Load a work tool's detailed description and schema |
| `scratchpad_put` | Create turn-local working memory from evidence |
| `scratchpad_update` | Update an existing scratchpad item |
| final memory mutation | Write/revise semantic graph memory |

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

Existing direct-child files of the validated session working root are seeded into read provenance at turn start. Nested files still require normal discovery through tools such as `file_tree`, `file_search`, or `code_search`.

## Document / image — owner

| Tool | Purpose |
| --- | --- |
| `document_read` | Read PDF / DOCX / TXT / MD / MARKDOWN |
| `image_analyze` | Analyze images with the vision model configured in `.env` |

## Code — owner

| Tool | Purpose |
| --- | --- |
| `code_index` | Build a compact Python repository index |
| `code_search` | Search files/symbols through the structural index |

## Terminal — owner

| Tool | Purpose |
| --- | --- |
| `terminal_command` | Execute a shell command on the current PC |

## Web / market — owner + trial

| Tool | Purpose |
| --- | --- |
| `latest_search` | Recency-focused public search |
| `web_research` | Search → public page reading → evidence package |
| `market_snapshot` | Market lookup/snapshot through configured providers |

---

# 7. Owner and Trial accounts

## Owner

- full work-tool catalog,
- PC filesystem / code / terminal access,
- direct document and image tools,
- upload/download,
- multiple persistent sessions.

## Trial

- independent per-user graph memory,
- core memory capabilities,
- web/market tools,
- attachment upload and automatic text/document/image analysis for uploaded attachments,
- no arbitrary host filesystem browsing/modification, terminal, or code tools,
- no download-link capability,
- one active persistent session per trial ID.

Trial uploads are separated by account directory:

```text
.mai_uploads/
├─ friend/
└─ family/
```

A new login using the same trial ID revokes the previous session.

---

# 8. Work continues if the browser leaves

Chats run as persistent jobs:

```text
/chat
→ persistent chat job
→ worker thread
→ browser polls job ID
```

The server task continues if the user switches apps or refreshes the page. Completed messages are restored from `/history`, and active work from `/chat/jobs`.

---

# 9. First-time installation — Windows, including non-developers

## 9.1 Install Git

Install Git for Windows, then verify in a new PowerShell window:

```powershell
git --version
```

## 9.2 Install Python

Install Python 3 and enable **Add Python to PATH** if available.

```powershell
python --version
pip --version
```

## 9.3 Install Ollama

Install and launch Ollama for Windows.

```powershell
ollama --version
```

### Minimum model requirement

The **minimum conversational model confirmed to work with Mai is `gemma4:e4b`**.

A smaller/weaker model may still generate ordinary text, but can become unreliable at Mai's structured JSON contracts, tool selection, `tool_manual`, multi-round context retention, and long-term-memory mutations. For actual use, **`gemma4:e4b` or a stronger model is recommended.**

```powershell
ollama pull gemma4:e4b
ollama pull gemma4:12b
```

`gemma4:12b` is the default image-analysis example.

If the Ollama service needs to be started manually:

```powershell
ollama serve
```

## 9.4 Clone the repository

```powershell
git clone https://github.com/FoggyCaligo/MAI_MyAI_sllm.git
cd MAI_MyAI_sllm
```

## 9.5 Create a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

## 9.6 Install packages

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

For development/pytest:

```powershell
pip install -r requirements-dev.txt
```

## 9.7 Create `.env`

```powershell
Copy-Item .env.example .env
```

Example:

```dotenv
MAI_OWNER_ID=owner
MAI_ALLOWED_USER_IDS=friend,family
MAI_OLLAMA_MODEL=gemma4:e4b
MAI_OLLAMA_IMAGE_MODEL=gemma4:12b
MAI_HOST=127.0.0.1
MAI_PORT=8000
```

## 9.8 Run locally

```powershell
python run_server.py
```

Open:

```text
http://127.0.0.1:8000/
```

---

# 10. Configure Tailscale for remote access — optional

Skip this section if Mai is used only on the same PC.

## 10.1 Install and sign in to Tailscale

Install Tailscale for Windows, then sign in from the Tailscale system-tray menu.

Verify:

```powershell
tailscale version
tailscale status
```

The local machine should appear connected in `tailscale status`.

## 10.2 Serve vs Funnel

- **Tailscale Serve** exposes Mai only to devices/users inside the same tailnet.
- **Tailscale Funnel** publishes Mai through a public HTTPS address reachable from the broader internet.

Mai's `start_public_tailscale.cmd` uses **Funnel**. Because Funnel is public internet exposure, configure owner/trial IDs carefully.

On first use, Tailscale may present a browser URL asking you to enable required HTTPS/MagicDNS/Funnel permissions for the tailnet. Follow the Tailscale setup flow shown by the CLI.

## 10.3 Run Mai with Funnel

From the project directory:

```powershell
.\start_public_tailscale.cmd
```

The script performs:

```text
check Tailscale connection
→ configure Funnel
→ print Funnel status/address
→ run Mai in the same terminal foreground
```

Because the Python server stays attached to that terminal, **pressing `Ctrl+C` in that window now shuts Mai down normally.**

Check Funnel status separately with:

```powershell
tailscale funnel status
```

If you want tailnet-only access instead, use Tailscale's official `serve` command to share `http://127.0.0.1:8000`. Serve/Funnel CLI behavior may change between Tailscale releases, so consult current Tailscale documentation if the CLI reports a different setup flow.

---

# 11. Graceful shutdown

## When started with `python run_server.py`

Press:

```text
Ctrl+C
```

in the same terminal.

## When started with `start_public_tailscale.cmd`

The current launcher also runs Python in the foreground. Press:

```text
Ctrl+C
```

in that launcher window.

Expected shutdown flow:

```text
Ctrl+C
→ Uvicorn shutdown
→ FastAPI lifespan cleanup
→ SQLite connections close
→ process exits
```

### If an older launcher already left a background server running

Find the PID listening on port 8000 and terminate it once:

```powershell
$pid = (Get-NetTCPConnection -LocalPort 8000 -State Listen).OwningProcess
Stop-Process -Id $pid
```

Then restart with the current launcher.

SQLite runs in WAL mode, so these runtime companion files may appear:

```text
graph.sqlite3
graph.sqlite3-wal
graph.sqlite3-shm
chat.sqlite3
chat.sqlite3-wal
chat.sqlite3-shm
```

Do not manually delete `-wal` or `-shm` while Mai is running.

See [`docs/OPERATIONS.md`](docs/OPERATIONS.md) for details.

---

# 12. Data and backups

## `data/graph.sqlite3`

Contains the core personal long-term memory:

- semantic graph nodes/edges,
- user anchor,
- support/conflict signals,
- durable graph source evidence,
- graph → source links.

## `data/chat.sqlite3`

Contains:

- raw conversation history,
- compact recent tool-operation history,
- authenticated sessions,
- persistent chat jobs.

For a complete state backup, shut Mai down normally and back up the entire `data/` directory.

---

# 13. Development testing

```powershell
python -m pytest -q
```

runs the full contract test suite.

Mai does not hide failures behind fallback behavior merely to make tests pass. When a runtime contract changes, test fixtures should explicitly satisfy the new required contract.

---

# 14. Documentation

- [`README.md`](README.md) — English default README
- [`README.ko.md`](README.ko.md) — canonical Korean README
- [`CONTRACT.md`](CONTRACT.md) — core runtime/product contract
- [`ROADMAP.md`](ROADMAP.md) — remaining development plan
- [`docs/`](docs/) — detailed operation and contract documents
