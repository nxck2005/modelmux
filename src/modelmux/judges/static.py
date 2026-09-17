"""Configured offline judge returning fixed scores."""

from __future__ import annotations

from modelmux.config import StaticScores
from modelmux.types import (
    QUESTION_VERSION,
    JudgeResult,
    ObservationKind,
    TaskProfile,
    TaskState,
)


class StaticTaskJudge:
    """Returns configured scores as a heuristic observation."""

    judge_id = "static"

    def __init__(self, scores: StaticScores) -> None:
        self._profile = TaskProfile(
            reasoning_demand=scores.reasoning_demand,
            routine=scores.routine,
            underspecified=scores.underspecified,
            high_consequence=scores.high_consequence,
        )

    async def evaluate(self, state: TaskState) -> JudgeResult:
        return JudgeResult(
            profile=self._profile,
            observation_kind=ObservationKind.HEURISTIC,
            judge_id=self.judge_id,
            question_version=QUESTION_VERSION,
        )
