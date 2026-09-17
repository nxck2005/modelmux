"""Contract schema and fixture tests for the versioned core boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from modelmux.router import (
    ApplicationResult,
    ApplicationStatus,
    EffectiveConfiguration,
    RouteDecision,
    RoutingRequest,
)
from modelmux.schemas import SCHEMA_MODELS
from modelmux.state import MessageRecord, extract_task_state
from modelmux.types import TaskState

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "v1"
FIXTURES = CONTRACTS / "fixtures"


def test_committed_schemas_match_authoritative_models() -> None:
    for filename, model in SCHEMA_MODELS.items():
        committed = json.loads((CONTRACTS / filename).read_text(encoding="utf-8"))
        assert committed == model.model_json_schema(), filename


@pytest.mark.parametrize(
    ("fixture", "model"),
    [
        ("routing-request.json", RoutingRequest),
        ("routing-decision.json", RouteDecision),
        ("application-result.json", ApplicationResult),
    ],
)
def test_fixtures_round_trip(fixture: str, model: type) -> None:
    text = (FIXTURES / fixture).read_text(encoding="utf-8")
    parsed = model.model_validate_json(text)
    assert model.model_validate_json(parsed.model_dump_json()) == parsed


def _configuration() -> EffectiveConfiguration:
    return EffectiveConfiguration(
        profile_id="normal",
        provider="example-provider",
        model="example-provider/normal-placeholder-1",
    )


def _state() -> TaskState:
    return extract_task_state([MessageRecord(role="user", text="do the thing")])


def test_applied_result_requires_actual_configuration() -> None:
    with pytest.raises(ValidationError):
        ApplicationResult(
            decision_id="d1",
            session_id="s1",
            user_turn_id="t1",
            session_revision=1,
            status=ApplicationStatus.APPLIED,
        )


@pytest.mark.parametrize(
    "status",
    [
        ApplicationStatus.APPLIED,
        ApplicationStatus.STALE,
        ApplicationStatus.CANCELLED,
    ],
)
def test_failure_code_only_valid_for_rejected(status: ApplicationStatus) -> None:
    with pytest.raises(ValidationError):
        ApplicationResult(
            decision_id="d1",
            session_id="s1",
            user_turn_id="t1",
            session_revision=1,
            status=status,
            applied=_configuration(),
            failure_code="apply_error",
        )


def test_rejected_result_reports_failure_without_claiming_switch() -> None:
    result = ApplicationResult(
        decision_id="d1",
        session_id="s1",
        user_turn_id="t1",
        session_revision=1,
        status=ApplicationStatus.REJECTED,
        applied=_configuration(),
        failure_code="unsupported_effort",
    )
    assert result.status is ApplicationStatus.REJECTED
    assert result.applied == _configuration()


def test_decision_is_a_proposal_not_an_acknowledgment() -> None:
    decision_fields = set(RouteDecision.model_fields)
    assert "applied" not in decision_fields
    assert "status" not in decision_fields
    assert {"status", "applied"} <= set(ApplicationResult.model_fields)


def test_request_failure_code_requires_failure_event() -> None:
    with pytest.raises(ValidationError):
        RoutingRequest(
            request_id="r",
            integration_instance_id="i",
            session_id="s",
            user_turn_id="t",
            session_revision=1,
            event_kind="new_user_turn",
            task_state=_state(),
            application_failure_code="provider_rejected",
        )
