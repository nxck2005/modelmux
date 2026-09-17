"""The judge contract."""

from __future__ import annotations

from typing import Protocol

from modelmux.types import JudgeResult, TaskState


class TaskJudge(Protocol):
    """Evaluates bounded task state.

    A judge never receives the model catalog and never recommends a model.
    """

    async def evaluate(self, state: TaskState) -> JudgeResult: ...
