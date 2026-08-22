# External Memory Model Experiment

This branch tests a stronger separation than the merged post-answer graph commit baseline.

## Runtime path

```text
WorkingMemoryLifecycle
  -> AgentLifecycle._run_agent_phase()
       -> answer only
  -> fixed answer
  -> separate memory model object
       -> GraphCommitPhase
  -> release fixed answer
```

The production runtime does not call `AgentLifecycle.run()` on this branch. Instead the outer working-memory lifecycle runs the agent phase directly and performs graph commit afterward.

## Separate model object

When the delegate model is `OllamaModel`, `WorkingMemoryLifecycle` creates a separate `OllamaModel` instance for memory work.

- default memory model name: same as `MAI_OLLAMA_MODEL`
- optional override: `MAI_OLLAMA_MEMORY_MODEL`
- same Ollama base URL and timeout

This allows testing whether a truly separate post-answer model invocation improves semantic graph extraction compared with the merged in-lifecycle baseline.
