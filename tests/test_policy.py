"""Tests for pure ordered policy evaluation."""

from __future__ import annotations

import pytest

from modelmux.config import PolicyCondition, PolicyRule
from modelmux.policy import match_rule
from modelmux.types import TaskProfile


def _profile(**overrides: float) -> TaskProfile:
    scores = {
        "reasoning_demand": 0.3,
        "routine": 0.3,
        "underspecified": 0.3,
        "high_consequence": 0.1,
    }
    scores.update(overrides)
    return TaskProfile(**scores)


def _rule(rule_id: str, *conditions: PolicyCondition) -> PolicyRule:
    return PolicyRule(id=rule_id, route=rule_id, conditions=conditions)


def test_first_matching_rule_wins() -> None:
    rules = (
        _rule("first", PolicyCondition(field="routine", op="gte", value=0.3)),
        _rule("second", PolicyCondition(field="routine", op="gte", value=0.0)),
    )

    matched = match_rule(_profile(routine=0.5), rules)

    assert matched is not None
    assert matched.id == "first"


def test_all_conditions_must_hold() -> None:
    rules = (
        _rule(
            "both",
            PolicyCondition(field="routine", op="gte", value=0.5),
            PolicyCondition(field="reasoning_demand", op="lte", value=0.2),
        ),
        _rule("fallback", PolicyCondition(field="routine", op="gte", value=0.0)),
    )

    matched = match_rule(_profile(routine=0.5, reasoning_demand=0.3), rules)

    assert matched is not None
    assert matched.id == "fallback"


@pytest.mark.parametrize(
    ("op", "expected"),
    [("gt", False), ("gte", True), ("lt", False), ("lte", True)],
)
def test_threshold_equality(op: str, expected: bool) -> None:
    rule = _rule("threshold", PolicyCondition(field="routine", op=op, value=0.65))

    matched = match_rule(_profile(routine=0.65), (rule,))

    assert (matched is not None) is expected


def test_no_match_returns_none() -> None:
    assert match_rule(_profile(), ()) is None
