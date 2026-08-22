# Context design summary

Mai keeps three different context layers with different lifetimes.

```text
Raw recent chat   -> conversational continuity
Semantic graph    -> durable meaning
Scratchpad        -> temporary working memory
```

The model receives recent raw chat and compact recent tool operations. Current-turn tool results are compacted only for model re-injection; original runtime events stay intact.

The semantic graph does not automatically mirror the chat transcript. Future graph provenance work will replace duplicated source text with stable references into raw source records so recalled graph facts can lead back to their originating chat/tool/web/scratchpad evidence.
