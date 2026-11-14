# Optional cloud model providers

The existing llama.cpp backend remains the default. The Owner may explicitly
select a cloud model from MAI's model picker. No fallback to a paid API occurs.

Place credentials in the local, git-ignored `.env`, never in `settings.yaml`.
Each provider needs a secret and a comma-separated allowlist of model IDs:

```dotenv
OPENAI_API_KEY=YOUR_OPENAI_SECRET
MAI_OPENAI_MODELS=gpt-4.1
ANTHROPIC_API_KEY=YOUR_ANTHROPIC_SECRET
MAI_ANTHROPIC_MODELS=claude-sonnet-4-5
OPENROUTER_API_KEY=YOUR_OPENROUTER_SECRET
MAI_OPENROUTER_MODELS=openai/gpt-4.1
CLOUD_MAX_OUTPUT_TOKENS=8192
```

Replace example model IDs with IDs currently supported by the relevant provider.
Only enabled models appear in the picker, named
`openai:MODEL_ID`, `anthropic:MODEL_ID`, `openrouter:MODEL_ID`.

Restart MAI after editing `.env`. The local llama.cpp model remains the default.
Cloud runs transmit prompts, tool outputs, and optional images to the selected
provider, and usage may incur charges. Keep the GAIA problem set, prompts,
tools, verification policy, and per-task timeout constant for fair comparison.
Cloud API requests use `MODEL_REQUEST_TIMEOUT_SECONDS` unless overridden.
Use private GAIA credentials and never commit dataset answer keys.
