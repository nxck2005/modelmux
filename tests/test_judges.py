"""Tests for the offline judges."""

from __future__ import annotations

from modelmux.config import StaticScores
from modelmux.judges.rules import RuleBasedTaskJudge
from modelmux.judges.static import StaticTaskJudge
from modelmux.state import MessageRecord, extract_task_state
from modelmux.types import QUESTION_VERSION, ObservationKind, TaskProfile, TaskState


def _state(text: str) -> TaskState:
    return extract_task_state([MessageRecord(role="user", text=text)])


async def test_static_judge_returns_configured_profile() -> None:
    scores = StaticScores(
        reasoning_demand=0.7,
        routine=0.1,
        underspecified=0.2,
        high_consequence=0.8,
    )

    result = await StaticTaskJudge(scores).evaluate(_state("anything"))

    assert result.profile == TaskProfile(
        reasoning_demand=0.7,
        routine=0.1,
        underspecified=0.2,
        high_consequence=0.8,
    )
    assert result.judge_id == "static"
    assert result.question_version == QUESTION_VERSION
    assert result.observation_kind is ObservationKind.HEURISTIC
    assert result.model_id is None


async def test_rules_judge_defaults_toward_normal() -> None:
    result = await RuleBasedTaskJudge().evaluate(_state("update the changelog"))
    profile = result.profile

    assert profile.reasoning_demand < 0.65
    assert profile.underspecified < 0.60
    assert profile.high_consequence < 0.70
    assert profile.routine < 0.90
    assert result.observation_kind is ObservationKind.HEURISTIC


async def test_rules_judge_escalates_reasoning_cues() -> None:
    result = await RuleBasedTaskJudge().evaluate(
        _state("diagnose the root cause of the flaky test")
    )
    assert result.profile.reasoning_demand >= 0.65


async def test_rules_judge_treats_explicit_mechanical_work_as_routine() -> None:
    result = await RuleBasedTaskJudge().evaluate(_state("rename the config key"))
    profile = result.profile

    assert profile.routine >= 0.90
    assert profile.reasoning_demand <= 0.20
    assert profile.underspecified <= 0.20


async def test_rules_judge_does_not_call_mixed_work_easy() -> None:
    result = await RuleBasedTaskJudge().evaluate(
        _state("rename the helper and diagnose the root cause")
    )
    profile = result.profile

    assert profile.reasoning_demand >= 0.65
    assert profile.routine < 0.90


async def test_rules_judge_flags_production_risk() -> None:
    result = await RuleBasedTaskJudge().evaluate(
        _state("migrate the production database")
    )
    assert result.profile.high_consequence >= 0.70


async def test_rules_judge_identity_is_heuristic() -> None:
    result = await RuleBasedTaskJudge().evaluate(_state("anything"))

    assert result.judge_id == "rules"
    assert result.question_version == QUESTION_VERSION
    assert result.observation_kind is ObservationKind.HEURISTIC
    assert result.model_id is None
