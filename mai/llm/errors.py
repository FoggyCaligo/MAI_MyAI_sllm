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


class ReasoningLoopDetected(ModelAdapterError):
    """A live reasoning stream repeats without answer/tool progress.

    The interrupted partial completion is never a valid assistant turn.
    Diagnostic metadata deliberately excludes incomplete tool arguments.
    """

    def __init__(self, diagnostics: dict) -> None:
        self.diagnostics = dict(diagnostics)
        super().__init__("repeated reasoning detected; interrupted current model turn")
