"""llama.cpp model adapter and provider-neutral contracts."""

from .models import ChatRequest, ModelConfig, ModelTurn, NativeToolCall
from .errors import ModelAdapterError, ModelRequestError, ModelProtocolError, ModelChatTimeoutError, ModelOutputLimitError
from .llamacpp import LlamaCppAdapter

__all__ = [
    "ChatRequest", "ModelConfig", "ModelTurn", "NativeToolCall",
    "ModelAdapterError", "ModelRequestError", "ModelProtocolError",
    "ModelChatTimeoutError", "ModelOutputLimitError", "LlamaCppAdapter",
]
