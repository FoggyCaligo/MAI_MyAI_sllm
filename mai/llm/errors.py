"""Provider-neutral model invocation errors."""

class ModelAdapterError(RuntimeError):
    """Base failure while invoking an inference backend."""


class ModelRequestError(ModelAdapterError):
    """The inference backend rejected a request."""


class ModelChatTimeoutError(ModelAdapterError):
    """The inference request timed out."""


class ModelProtocolError(ModelAdapterError):
    """The inference response violates the declared protocol."""


class ModelOutputLimitError(ModelProtocolError):
    """Inference output reached its configured budget."""
