# Dedicated Memory Model Contract

Mai uses separate model invocations for user-facing answer generation and durable semantic graph commitment.

## Runtime order

```text
WorkingMemoryLifecycle
  -> agent model loop
       -> tools / recall / answer
  -> fixed answer
  -> dedicated memory model
       -> GraphCommitPhase
       -> write_memory or revise_memory
  -> memory success
  -> release exact fixed answer
```

The memory model does not produce or rewrite the user-facing answer. The answer is already fixed before graph commitment begins.

## Model configuration

```env
MAI_OLLAMA_MODEL=gemma4:e4b
MAI_OLLAMA_MEMORY_MODEL=qwen3.5:9b
```

- `MAI_OLLAMA_MODEL` controls the normal conversation/work agent.
- `MAI_OLLAMA_MEMORY_MODEL` controls the post-answer semantic graph commit model.
- The default memory model is `qwen3.5:9b`.
- The memory model uses the same configured Ollama base URL and timeout as the conversation model.
- A separate `OllamaModel` object is created for memory work; the two stages do not share one model object.

## Memory context

`GraphCommitPhase` receives only the bounded post-answer memory context required by the memory contract, including the current user text, fixed answer, recalled graph context, selected scratchpad context, and prior mutation results when another memory round is required.

Ambient recent chat, prior work-tool operations, working root, and attachment context are isolated from this model call unless they were explicitly promoted into the memory inputs by the existing contracts.

## Failure behavior

Memory commitment is mandatory for a completed turn. If the dedicated memory model or graph mutation fails, the fixed answer is not converted into a successful response through a fallback.

No string heuristic may infer names, identities, relations, correction intent, or graph routes. Semantic node/relation choices remain model-authored; framework code only enforces structural scope, provenance, and execution contracts.

## Rationale

Local live testing showed that `qwen3.5:9b` produced materially better concrete semantic graph nodes for identity/name facts than using `gemma4:e4b` for both stages. The dedicated model also completed the tested two-call path faster than the same-model configuration. This contract preserves that observed separation while keeping the model name configurable through `.env`.
