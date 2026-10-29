"""Per-request factual execution ledger; never infers goal completion from tool success."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4


class TaskLifecycle(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class GoalCompletion(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"


class AnswerVerification(str, Enum):
    NOT_RUN = "not_run"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class TaskObservation:
    execution_index: int
    tool_name: str
    ok: bool
    error_type: str | None
    content_fingerprint: str | None


@dataclass(slots=True)
class TaskState:
    goal: str
    task_id: str = field(default_factory=lambda: uuid4().hex)
    lifecycle: TaskLifecycle = TaskLifecycle.CREATED
    observations: list[TaskObservation] = field(default_factory=list)
    answer_verification: AnswerVerification = AnswerVerification.NOT_RUN
    goal_completion: GoalCompletion = GoalCompletion.UNVERIFIED
    failure_type: str | None = None

    def start(self) -> None:
        if self.lifecycle is not TaskLifecycle.CREATED:
            raise RuntimeError("task may only start once")
        self.lifecycle = TaskLifecycle.RUNNING

    def observe(self, observation: TaskObservation) -> None:
        if self.lifecycle is not TaskLifecycle.RUNNING:
            raise RuntimeError("cannot record an observation outside a running task")
        if observation.execution_index != len(self.observations):
            raise ValueError("task observations must follow tool execution order")
        self.observations.append(observation)

    def verify_answer(self, *, accepted: bool) -> None:
        if self.lifecycle is not TaskLifecycle.RUNNING:
            raise RuntimeError("cannot verify answer outside a running task")
        self.answer_verification = (
            AnswerVerification.ACCEPTED if accepted else AnswerVerification.REJECTED
        )

    def complete(self) -> None:
        if self.lifecycle is not TaskLifecycle.RUNNING:
            raise RuntimeError("cannot complete a task that is not running")
        if self.answer_verification is not AnswerVerification.ACCEPTED:
            raise RuntimeError("task response cannot complete without accepted verification")
        # A correct answer does not establish that external effects achieved the goal.
        self.lifecycle = TaskLifecycle.COMPLETED

    def fail(self, cause: Exception) -> None:
        if self.lifecycle is not TaskLifecycle.RUNNING:
            raise RuntimeError("cannot fail a task that is not running")
        self.failure_type = type(cause).__name__
        self.lifecycle = TaskLifecycle.FAILED
