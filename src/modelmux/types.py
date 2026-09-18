"""Harness-neutral core types shared by configuration, judges, and routing."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]

LATEST_USER_CHARS = 6_000
INSTRUCTIONS_CHARS = 2_000
HISTORY_CHARS = 3_000
TOOLS_CHARS = 1_000

MAX_INSTRUCTION_RECORDS = 32
MAX_HISTORY_RECORDS = 64
MAX_TOOL_RECORDS = 32
MAX_TOOL_NAMES = 32
MAX_SOURCE_CHARS = 32

# Enough for the four category budgets under worst-case JSON escaping (a
# control character costs six ASCII bytes) plus bounded labels and counts.
MAX_STATE_BYTES = 128 * 1024

SourceLabel = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_SOURCE_CHARS),
]


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
    """Text retained for judging, with its source and truncation facts.

    ``source_truncated`` records that the source label was shortened to the
    label cap; removed label characters are never counted in ``omitted_chars``.
    """

    model_config = _STRICT

    source: SourceLabel
    source_truncated: bool
    text: Annotated[str, Field(max_length=LATEST_USER_CHARS)]
    truncated: bool
    omitted_chars: NonNegativeInt


class TaskState(BaseModel):
    """Bounded, harness-neutral task content supplied to a judge.

    Text budgets are enforced per category, retained record counts and source
    labels have fixed caps, and the serialized state must fit
    ``MAX_STATE_BYTES``. The category text sums and the serialized byte cap
    are Python-side checks that standard JSON Schema cannot express.
    Measured counts (``message_count``, ``input_bytes``, ``tool_count``)
    describe the supplied input and are independent of what was retained.
    ``tool_names_truncated`` counts retained names whose labels were shortened;
    label loss never contributes to body-text ``omitted_chars``.
    """

    model_config = _STRICT

    latest_user: BoundedText | None
    instructions: Annotated[
        tuple[BoundedText, ...], Field(max_length=MAX_INSTRUCTION_RECORDS)
    ]
    history: Annotated[tuple[BoundedText, ...], Field(max_length=MAX_HISTORY_RECORDS)]
    tool_excerpts: Annotated[
        tuple[BoundedText, ...], Field(max_length=MAX_TOOL_RECORDS)
    ]
    instructions_omitted: NonNegativeInt
    history_omitted: NonNegativeInt
    tools_omitted: NonNegativeInt
    message_count: NonNegativeInt
    input_bytes: NonNegativeInt
    tool_count: NonNegativeInt
    tool_names: Annotated[tuple[SourceLabel, ...], Field(max_length=MAX_TOOL_NAMES)]
    tool_names_omitted: NonNegativeInt
    tool_names_truncated: NonNegativeInt

    @model_validator(mode="after")
    def _within_bounds(self) -> TaskState:
        categories = (
            (
                "latest_user",
                () if self.latest_user is None else (self.latest_user,),
                LATEST_USER_CHARS,
            ),
            ("instructions", self.instructions, INSTRUCTIONS_CHARS),
            ("history", self.history, HISTORY_CHARS),
            ("tool_excerpts", self.tool_excerpts, TOOLS_CHARS),
        )
        for name, blocks, budget in categories:
            if sum(len(block.text) for block in blocks) > budget:
                raise ValueError(f"{name} text exceeds {budget} characters")
            for block in blocks:
                if block.truncated != (block.omitted_chars > 0):
                    raise ValueError("truncated must match omitted_chars")
        if len(self.model_dump_json().encode("utf-8")) > MAX_STATE_BYTES:
            raise ValueError(f"serialized task state exceeds {MAX_STATE_BYTES} bytes")
        return self


class JudgeUsage(BaseModel):
    """Token usage reported by a judge. Unknown values are None."""

    model_config = _STRICT

    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None


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
    usage: JudgeUsage | None = None
