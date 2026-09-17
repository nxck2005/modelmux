"""Harness-neutral core types shared by configuration, judges, and routing."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


def _numeric_score(value: object) -> object:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("score must be a number")
    return value


Score = Annotated[
    float,
    BeforeValidator(_numeric_score),
    Field(ge=0.0, le=1.0, allow_inf_nan=False),
]

_STRICT = ConfigDict(extra="forbid", frozen=True)

QUESTION_VERSION: Final[str] = "task-profile-v1"


class ObservationKind(StrEnum):
    """Whether scores are model probabilities or a heuristic."""

    PROBABILITY = "probability"
    HEURISTIC = "heuristic"


class TaskProfile(BaseModel):
    """Four independent finite scores in [0, 1].

    The scores are not a distribution and do not sum to one.
    """

    model_config = _STRICT

    reasoning_demand: Score
    routine: Score
    underspecified: Score
    high_consequence: Score


class BoundedText(BaseModel):
    """Text retained for judging, with its source and truncation facts."""

    model_config = _STRICT

    source: Identifier
    text: str
    truncated: bool
    omitted_chars: NonNegativeInt


class TaskState(BaseModel):
    """Bounded, harness-neutral task content supplied to a judge.

    Counts and byte sizes describe only the records supplied to extraction.
    They are exact for that provided window and say nothing about the full
    session or the provider generation request.
    """

    model_config = _STRICT

    latest_user: BoundedText | None
    instructions: tuple[BoundedText, ...]
    history: tuple[BoundedText, ...]
    tool_excerpts: tuple[BoundedText, ...]
    instructions_omitted: NonNegativeInt
    history_omitted: NonNegativeInt
    tools_omitted: NonNegativeInt
    message_count: NonNegativeInt
    input_bytes: NonNegativeInt
    tool_count: NonNegativeInt
    tool_names: tuple[Identifier, ...]


class JudgeResult(BaseModel):
    """One judge observation of a task profile.

    ``model_id`` is the judge model identity when one exists; offline judges
    leave it None. A judge result never names a model to route to.
    """

    model_config = _STRICT

    profile: TaskProfile
    observation_kind: ObservationKind
    judge_id: Identifier
    question_version: Identifier = QUESTION_VERSION
    model_id: Identifier | None = None
