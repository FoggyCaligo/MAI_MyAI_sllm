"""Strict configuration loading: local secrets in .env, non-secret defaults in YAML."""
from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Mapping

import yaml

# The YAML file is deliberately a flat mapping of existing environment-setting names.
# This keeps the pre-existing typed validation at each consumer intact.
_ALLOWED_KEYS = frozenset((
    "LLM_BACKEND", "LLAMACPP_HOST", "OLLAMA_HOST", "MAIN_MODEL",
    "OLLAMA_REQUEST_TIMEOUT_SECONDS", "VISION_MODEL", "OLLAMA_THINK",
    "MAI_DEBUG_MODEL_INPUT", "AGENT_MAX_IDENTICAL_CALLS",
    "AGENT_MAX_IDENTICAL_FAILURES", "AGENT_MAX_NO_PROGRESS_ROUNDS",
    "TOOL_TIMEOUT_SECONDS", "TERMINAL_TIMEOUT_SECONDS",
    "MAX_INLINE_TOOL_RESULT_CHARS", "MEMORY_RECALL_INCLUDE_UTTERANCES",
    "SESSION_HISTORY_MESSAGES", "MAI_ANSWER_QUALITY_REVIEW",
    "VOICE_SILENCE_SECONDS", "VOICE_MIN_SPEECH_SECONDS",
    "VOICE_MATCH_THRESHOLD", "VOICE_WHISPER_MODEL",
))


class _UniqueMappingLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: yaml.SafeLoader, node: yaml.nodes.MappingNode) -> dict[str, object]:
    result: dict[str, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str):
            raise ValueError("settings.yaml keys must be strings")
        if key in result:
            raise ValueError(f"settings.yaml contains duplicate key: {key}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueMappingLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping,
)


def load_settings(path: Path, *, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Parse settings explicitly and fill only absent process environment entries."""
    content = path.read_text(encoding="utf-8-sig")
    parsed = yaml.load(content, Loader=_UniqueMappingLoader)
    if not isinstance(parsed, Mapping):
        raise TypeError("settings.yaml must contain a top-level mapping")
    unknown = set(parsed).difference(_ALLOWED_KEYS)
    if unknown:
        raise ValueError(f"unknown settings.yaml keys: {', '.join(sorted(unknown))}")
    result: dict[str, str] = {}
    for key, value in parsed.items():
        if isinstance(value, bool):
            encoded = "true" if value else "false"
        elif isinstance(value, (int, float, str)) and not isinstance(value, complex):
            encoded = str(value)
        else:
            raise TypeError(f"settings.yaml {key} must be a string, number, or boolean")
        if not encoded.strip():
            raise ValueError(f"settings.yaml {key} must not be blank")
        result[key] = encoded
    target = os.environ if environ is None else environ
    for key, value in result.items():
        target.setdefault(key, value)
    return result
