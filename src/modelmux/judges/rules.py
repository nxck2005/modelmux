"""Conservative, transparent cue-based judge."""

from __future__ import annotations

from modelmux.types import (
    QUESTION_VERSION,
    JudgeResult,
    ObservationKind,
    TaskProfile,
    TaskState,
)

_BASELINE_REASONING_DEMAND = 0.35
_BASELINE_ROUTINE = 0.40
_BASELINE_UNDERSPECIFIED = 0.30
_BASELINE_HIGH_CONSEQUENCE = 0.05

_REASONING_CUES = ("diagnose", "root cause", "design", "architect", "race condition")
_MECHANICAL_CUES = ("rename", "typo", "reformat", "lint")
_AMBIGUOUS_CUES = ("make it better", "as needed", "something like", "not sure")
_CONSEQUENCE_CUES = (
    "production",
    "migration",
    "delete data",
    "drop table",
    "credentials",
    "secrets",
)


class RuleBasedTaskJudge:
    """Heuristic baseline using a small explicit cue table.

    Only the latest user text is inspected. Scores are not calibrated
    probabilities, and mechanical work is treated as routine only when no
    reasoning or high-consequence cue is present.
    """

    judge_id = "rules"

    async def evaluate(self, state: TaskState) -> JudgeResult:
        text = state.latest_user.text.casefold() if state.latest_user else ""
        return JudgeResult(
            profile=_score(text),
            observation_kind=ObservationKind.HEURISTIC,
            judge_id=self.judge_id,
            question_version=QUESTION_VERSION,
        )


def _score(text: str) -> TaskProfile:
    reasoning_demand = _BASELINE_REASONING_DEMAND
    routine = _BASELINE_ROUTINE
    underspecified = _BASELINE_UNDERSPECIFIED
    high_consequence = _BASELINE_HIGH_CONSEQUENCE

    reasoning_cue = _contains(text, _REASONING_CUES)
    consequence_cue = _contains(text, _CONSEQUENCE_CUES)

    if _contains(text, _MECHANICAL_CUES) and not (reasoning_cue or consequence_cue):
        reasoning_demand, routine, underspecified = 0.10, 0.90, 0.10
    if reasoning_cue:
        reasoning_demand = 0.70
    if consequence_cue:
        high_consequence = 0.80
    if _contains(text, _AMBIGUOUS_CUES):
        underspecified = 0.65

    return TaskProfile(
        reasoning_demand=reasoning_demand,
        routine=routine,
        underspecified=underspecified,
        high_consequence=high_consequence,
    )


def _contains(text: str, cues: tuple[str, ...]) -> bool:
    return any(cue in text for cue in cues)
