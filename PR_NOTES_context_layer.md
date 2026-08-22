# Context layer PR notes

This branch restores the MK4-style short-term context layer without weakening Mai's current graph/runtime contracts.

Implemented:

- inject the previous 10 raw user/assistant messages into model context
- persist and inject the previous 5 compact tool operations
- compact current-turn tool results only at the model boundary
- preserve original runtime work events
- inject current local date into every model request
- document the separation of raw dialogue, durable semantic graph, and future scratchpad working memory
- add roadmap items for successful-action dedup, autonomy retry, web grounding, persistent sessions, owner/trial restrictions, working directory, automatic attachment processing, scratchpad, and graph source provenance

Not implemented in this branch:

- successful-action dedup
- autonomy retry
- web grounding pass
- persistent session/job execution
- owner/trial tool restrictions
- session working directory
- automatic attachment processing
- scratchpad
- graph source reference migration

Those remain separate follow-up changes so this PR can be tested independently.
