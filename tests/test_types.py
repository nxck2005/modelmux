"""Tests for the core task profile and judge result types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from modelmux.types import JudgeResult, TaskProfile

VALID_SCORES = {
    "reasoning_demand": 0.5,
    "routine": 0.5,
    "underspecified": 0.5,
    "high_consequence": 0.5,
}


@pytest.mark.parametrize(
    "value",
    [True, False, "0.5", float("nan"), float("inf"), float("-inf"), -0.01, 1.01],
)
def test_rejects_invalid_observations(value: object) -> None:
    with pytest.raises(ValidationError):
        TaskProfile(**{**VALID_SCORES, "reasoning_demand": value})


def test_rejects_missing_observation_field() -> None:
    incomplete = {key: value for key, value in VALID_SCORES.items() if key != "routine"}
    with pytest.raises(ValidationError):
        TaskProfile(**incomplete)


def test_accepts_integer_scores() -> None:
    profile = TaskProfile(
        reasoning_demand=0,
        routine=1,
        underspecified=0,
        high_consequence=1,
    )
    assert profile.routine == 1.0


def test_judge_result_rejects_unknown_observation_kind() -> None:
    with pytest.raises(ValidationError):
        JudgeResult(
            profile=TaskProfile(**VALID_SCORES),
            observation_kind="calibrated",
            judge_id="test",
        )
