"""Provider-neutral errors surfaced by the portable llama.cpp adapter."""


class ModelAdapterError(RuntimeError):
    """Base failure of the single inference backend."""


class ModelRequestError(ModelAdapterError):
    """The model server rejected or failed a request."""


class ModelTimeoutError(ModelAdapterError):
    """One chat request exceeded its configured deadline."""


class ModelProtocolError(ModelAdapterError):
    """The model server returned a malformed native tool/chat response."""
