"""Pure evaluation of ordered policy rules."""

from __future__ import annotations

import operator
from collections.abc import Callable, Sequence

from modelmux.config import (
    ComparisonOperator,
    PolicyCondition,
    PolicyRule,
    SemanticField,
)
from modelmux.types import TaskProfile

_COMPARISONS: dict[ComparisonOperator, Callable[[float, float], bool]] = {
    ComparisonOperator.GT: operator.gt,
    ComparisonOperator.GTE: operator.ge,
    ComparisonOperator.LT: operator.lt,
    ComparisonOperator.LTE: operator.le,
}


def match_rule(profile: TaskProfile, rules: Sequence[PolicyRule]) -> PolicyRule | None:
    """Return the first rule whose conditions all hold, or None."""
    for rule in rules:
        if all(_condition_holds(profile, condition) for condition in rule.conditions):
            return rule
    return None


def _condition_holds(profile: TaskProfile, condition: PolicyCondition) -> bool:
    return _COMPARISONS[condition.op](
        _profile_value(profile, condition.field), condition.value
    )


def _profile_value(profile: TaskProfile, field: SemanticField) -> float:
    values = {
        SemanticField.REASONING_DEMAND: profile.reasoning_demand,
        SemanticField.ROUTINE: profile.routine,
        SemanticField.UNDERSPECIFIED: profile.underspecified,
        SemanticField.HIGH_CONSEQUENCE: profile.high_consequence,
    }
    return values[field]
