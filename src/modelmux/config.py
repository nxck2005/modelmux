"""Validated TOML configuration for the modelmux routing core."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from modelmux.types import Identifier, NonNegativeInt, Score

EnvVarName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]

_STRICT = ConfigDict(extra="forbid", frozen=True)


class ConfigError(ValueError):
    """Configuration could not be read, parsed, or validated.

    The original parser or validation exception is suppressed so its message
    cannot expose credential values or raw configuration fragments.
    """

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(self.problems))


class SemanticField(StrEnum):
    """The four independent task-profile scores used by policy conditions."""

    REASONING_DEMAND = "reasoning_demand"
    ROUTINE = "routine"
    UNDERSPECIFIED = "underspecified"
    HIGH_CONSEQUENCE = "high_consequence"


class ComparisonOperator(StrEnum):
    """Threshold comparisons allowed in policy conditions."""

    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"


class JudgeKind(StrEnum):
    """Task judges that can be selected."""

    STATIC = "static"
    RULES = "rules"
    JEV = "jev"


class OutputLimits(BaseModel):
    """Optional per-profile output token limits."""

    model_config = _STRICT

    default: PositiveInt | None = None
    max: PositiveInt | None = None

    @model_validator(mode="after")
    def _consistent(self) -> OutputLimits:
        if (
            self.default is not None
            and self.max is not None
            and self.default > self.max
        ):
            raise ValueError("output default limit must not exceed max limit")
        return self


class ModelProfile(BaseModel):
    """A user-configured provider model the router may select."""

    model_config = _STRICT

    provider: Identifier
    model: Identifier
    rank: NonNegativeInt
    capabilities: tuple[Identifier, ...]
    reasoning_effort: Identifier | None = None
    output: OutputLimits | None = None
    context_limit: PositiveInt | None = None


class PolicyCondition(BaseModel):
    """One threshold comparison in a policy rule."""

    model_config = _STRICT

    field: SemanticField
    op: ComparisonOperator
    value: Score


class PolicyRule(BaseModel):
    """A named, ordered rule whose conditions must all hold."""

    model_config = _STRICT

    id: Identifier
    route: Identifier
    conditions: Annotated[tuple[PolicyCondition, ...], Field(min_length=1)]


class PolicyConfig(BaseModel):
    """Ordered policy rules evaluated first match first."""

    model_config = _STRICT

    rules: tuple[PolicyRule, ...] = ()

    @model_validator(mode="after")
    def _unique_rule_ids(self) -> PolicyConfig:
        seen: set[str] = set()
        duplicates: list[str] = []
        for rule in self.rules:
            if rule.id in seen:
                duplicates.append(rule.id)
            seen.add(rule.id)
        if duplicates:
            raise ValueError(f"duplicate policy rule id(s): {', '.join(duplicates)}")
        return self


class RouterReferences(BaseModel):
    """Profile references the router falls back to.

    All four are required so the router never has to infer a profile.
    """

    model_config = _STRICT

    default: Identifier
    judgment_fallback: Identifier
    compatibility_fallback: Identifier
    economy: Identifier


class StaticScores(BaseModel):
    """Fixed scores returned by the static judge."""

    model_config = _STRICT

    reasoning_demand: Score
    routine: Score
    underspecified: Score
    high_consequence: Score


class StaticJudgeConfig(BaseModel):
    """Offline static judge settings."""

    model_config = _STRICT

    scores: StaticScores


class JevJudgeConfig(BaseModel):
    """Jev judge settings.

    ``api_key_env`` is the name of the environment variable holding the bearer
    credential. Configuration loading never reads that variable; the Jev judge
    resolves it when instantiated.
    """

    model_config = _STRICT

    api_key_env: EnvVarName
    model: Identifier = "jev-latest"


class JudgeConfig(BaseModel):
    """Judge selection and evaluation deadline."""

    model_config = _STRICT

    selection: JudgeKind
    deadline_ms: PositiveInt = 1500
    static: StaticJudgeConfig | None = None
    jev: JevJudgeConfig | None = None

    @model_validator(mode="after")
    def _selection_requirements(self) -> JudgeConfig:
        if self.selection is JudgeKind.STATIC and self.static is None:
            raise ValueError(
                "judge selection 'static' requires a [judge.static.scores] section"
            )
        if self.selection is JudgeKind.JEV and self.jev is None:
            raise ValueError("judge selection 'jev' requires a [judge.jev] section")
        return self


class RouterConfig(BaseModel):
    """Validated router configuration loaded from a TOML file."""

    model_config = _STRICT

    version: Literal[1]
    router: RouterReferences
    judge: JudgeConfig
    profiles: dict[Identifier, ModelProfile]
    policy: PolicyConfig = Field(default_factory=PolicyConfig)

    @model_validator(mode="after")
    def _references_exist(self) -> RouterConfig:
        problems: list[str] = []
        references = (
            ("router.default", self.router.default),
            ("router.judgment_fallback", self.router.judgment_fallback),
            ("router.compatibility_fallback", self.router.compatibility_fallback),
            ("router.economy", self.router.economy),
        )
        for location, profile_id in references:
            if profile_id not in self.profiles:
                problems.append(f"{location}: unknown profile {profile_id!r}")
        for rule in self.policy.rules:
            if rule.route not in self.profiles:
                problems.append(
                    f"policy.rules[{rule.id}].route: unknown profile {rule.route!r}"
                )
        if problems:
            raise ValueError("; ".join(problems))
        return self


def load_config(path: str | Path) -> RouterConfig:
    """Load and validate a TOML router configuration file."""
    config_path = Path(path)
    try:
        with config_path.open("rb") as file:
            raw = tomllib.load(file)
    except OSError as exc:
        reason = exc.strerror or "unreadable file"
        raise ConfigError(
            [f"{config_path}: cannot read configuration file: {reason}"]
        ) from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            [f"{config_path}: invalid TOML syntax at {_error_location(exc)}"]
        ) from None
    try:
        return RouterConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(
            [f"{config_path}: {problem}" for problem in _problems(exc)]
        ) from None


def _error_location(exc: tomllib.TOMLDecodeError) -> str:
    match = re.search(r"line (\d+), column (\d+)", str(exc))
    if match is None:
        return "unknown position"
    return f"line {match.group(1)}, column {match.group(2)}"


def _problems(exc: ValidationError) -> list[str]:
    problems: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        message = error["msg"]
        problems.append(f"{location}: {message}" if location else message)
    return problems
