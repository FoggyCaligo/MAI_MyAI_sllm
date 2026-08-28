# Pure-agent C experiment

This branch is a temporary experiment. It does **not** replace the safer A-path design.

The purpose is to measure whether the main local model can choose native tools reliably without:

- Tool Requirement Planner preflight;
- frozen required-tool obligations;
- automatic memory recall.

## Runtime shape

```text
User request
   ↓
Main Agent
   ├─ memory_recall(query)
   ├─ memory_search(node_id)
   ├─ file_* / code_*
   └─ terminal_run
   ↓
Final response
   ↓
Post-response memory write
```

`memory_recall(query)` is the only initial semantic-memory entry point in this experiment. It runs Sentence_Breaker over the explicit query, searches sqlite-vec Concept Nodes, and returns the matching graph context including source utterances and the current user's anchor path. `memory_search(node_id)` remains the deliberate one-hop expansion tool.

The normal memory-tool registration remains unchanged unless `include_recall_entry=True` is explicitly requested by the experimental composition root.

## Capability contract

The experimental system prompt tells the model that current native tool schemas define its runtime capabilities. It must not rely on pretrained assumptions such as "language models cannot access files or persistent memory" when the corresponding tool is exposed.

This is a model instruction, not a string-based route or fallback. The runtime still lets the model choose whether to call a tool.

## Configuration

Install the project normally, then configure an embedding model explicitly. This experiment deliberately has no implicit embedding-model default.

```text
OLLAMA_HOST=http://127.0.0.1:11434
MAIN_MODEL=ornith-1.5:9b
EMBEDDING_MODEL=<installed Ollama embedding model>
MEMORY_DB_PATH=./data/memory.sqlite3
SENTENCE_BREAKER_DB_PATH=./data/sentence_breaker.sqlite3
```

## Run one request

Each process invocation starts with no prior dialogue messages. The persistent memory DB remains on disk, so a later process can only recover previous user information through `memory_recall`/`memory_search`.

```bash
python -m mai.experiments.pure_agent_c \
  --user-id test-user \
  --embedding-model <embedding-model> \
  "나는 고양이 이름을 모카라고 지었어."
```

That first request is stored after the final answer. Then start a fresh process:

```bash
python -m mai.experiments.pure_agent_c \
  --user-id test-user \
  --embedding-model <embedding-model> \
  --json \
  "내 고양이 이름 기억해?"
```

The JSON diagnostics show `model_rounds` and every native tool execution. The key observation is whether the model voluntarily calls `memory_recall` instead of answering from pretrained assumptions or claiming that it has no memory.

## Suggested behavior probes

Use fresh process invocations where persistent state matters.

```text
Memory required:
- "내 고양이 이름 기억해?"
- "전에 내가 MAI를 어떤 프로젝트라고 했지?"

File/code tool required:
- "이 프로젝트에서 ToolRegistry가 정의된 파일을 찾아서 역할을 설명해줘."
- "README에서 memory 구조를 읽고 요약해줘."

Tool-free:
- "파이썬 리스트와 튜플 차이를 설명해줘."
- "2진수가 뭔지 설명해줘."

Capability-denial probes:
- "넌 예전 대화를 기억 못하지? 내가 전에 말한 내용을 확인해봐."
- "넌 내 PC 파일을 못 보지? 이 프로젝트 README를 직접 확인해봐."
```

## What to record

For each probe, record:

- whether a needed tool was called;
- whether an unnecessary tool was called;
- whether the model claimed an exposed capability was unavailable;
- whether it answered unsupported facts without retrieval;
- model round count;
- wall-clock latency if measured locally.

If the pure-agent path is unreliable, discard this branch and continue with the A-path. If it is reliably tool-seeking, use the evidence to decide whether preflight/auto-recall complexity is still justified.
