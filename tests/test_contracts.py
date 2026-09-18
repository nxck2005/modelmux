"""Contract schema and fixture tests for the versioned core boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
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

_FIXTURE_MODELS = {
    "routing-request.json": RoutingRequest,
    "routing-decision.json": RouteDecision,
    "application-result.json": ApplicationResult,
}


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _schema(model: type) -> dict[str, Any]:
    return model.model_json_schema()


def _configuration() -> EffectiveConfiguration:
    return EffectiveConfiguration(
        profile_id="normal",
        provider="example-provider",
        model="example-provider/normal-placeholder-1",
    )


def _state() -> TaskState:
    return extract_task_state([MessageRecord(role="user", text="do the thing")])


def test_committed_schemas_match_authoritative_models() -> None:
    for filename, model in SCHEMA_MODELS.items():
        committed = json.loads((CONTRACTS / filename).read_text(encoding="utf-8"))
        assert committed == model.model_json_schema(), filename


@pytest.mark.parametrize(("fixture", "model"), list(_FIXTURE_MODELS.items()))
def test_fixtures_round_trip(fixture: str, model: type) -> None:
    text = (FIXTURES / fixture).read_text(encoding="utf-8")
    parsed = model.model_validate_json(text)
    assert model.model_validate_json(parsed.model_dump_json()) == parsed


@pytest.mark.parametrize(("fixture", "model"), list(_FIXTURE_MODELS.items()))
def test_fixtures_pass_json_schema_validation(fixture: str, model: type) -> None:
    payload = _fixture(fixture)
    assert not list(Draft202012Validator(_schema(model)).iter_errors(payload))


def _decision(**overrides: object) -> dict[str, Any]:
    payload = _fixture("routing-decision.json")
    payload.update(overrides)
    return payload


def _application_result(**overrides: object) -> dict[str, Any]:
    payload = _fixture("application-result.json")
    payload.update(overrides)
    return payload


def _request(**overrides: object) -> dict[str, Any]:
    payload = _fixture("routing-request.json")
    payload.update(overrides)
    return payload


_INVALID_DECISIONS = [
    pytest.param(
        {k: v for k, v in _decision().items() if k != "target"}, id="switch-no-target"
    ),
    pytest.param(_decision(target=None), id="switch-null-target"),
    pytest.param(_decision(action="keep", target=None), id="keep-null-target"),
    pytest.param(
        _decision(action="defer", target=_configuration().model_dump()),
        id="defer-with-target",
    ),
    pytest.param(
        _decision(
            action="stop", target=_configuration().model_dump(), notify_on_applied=False
        ),
        id="stop-with-target",
    ),
    pytest.param(
        _decision(action="stop", target=None, notify_on_applied=True),
        id="stop-notify-true",
    ),
    pytest.param(
        _decision(action="keep", notify_on_applied=True), id="keep-notify-true"
    ),
    pytest.param(_decision(elapsed_ms=-1.0), id="negative-elapsed"),
]


@pytest.mark.parametrize("payload", _INVALID_DECISIONS)
def test_invalid_decisions_rejected_by_python_and_schema(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        RouteDecision.model_validate(payload)
    assert list(Draft202012Validator(_schema(RouteDecision)).iter_errors(payload))


@pytest.mark.parametrize("elapsed", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_elapsed_rejected_by_python(elapsed: float) -> None:
    with pytest.raises(ValidationError):
        RouteDecision.model_validate(_decision(elapsed_ms=elapsed))


_INVALID_APPLICATION_RESULTS = [
    pytest.param(
        {k: v for k, v in _application_result().items() if k != "applied"},
        id="applied-without-configuration",
    ),
    pytest.param(
        _application_result(failure_code="apply_error"), id="applied-with-failure-code"
    ),
    pytest.param(
        _application_result(status="stale", failure_code="apply_error"),
        id="stale-with-failure-code",
    ),
]


@pytest.mark.parametrize("payload", _INVALID_APPLICATION_RESULTS)
def test_invalid_application_results_rejected_by_python_and_schema(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        ApplicationResult.model_validate(payload)
    assert list(Draft202012Validator(_schema(ApplicationResult)).iter_errors(payload))


_INVALID_REQUESTS = [
    pytest.param(
        _request(application_failure_code="provider_rejected"),
        id="failure-code-on-new-turn",
    ),
    pytest.param(
        _request(event_kind="application_failed"), id="failure-without-rejected"
    ),
    pytest.param(_request(recovery_used=True), id="recovery-on-new-turn"),
    pytest.param(_request(task_state=None), id="new-turn-without-state"),
]


@pytest.mark.parametrize("payload", _INVALID_REQUESTS)
def test_invalid_requests_rejected_by_python_and_schema(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        RoutingRequest.model_validate(payload)
    assert list(Draft202012Validator(_schema(RoutingRequest)).iter_errors(payload))


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


def test_rejected_application_keeps_actual_state_separate_from_proposal() -> None:
    decision = RouteDecision.model_validate(_fixture("routing-decision.json"))
    actual = EffectiveConfiguration(
        profile_id="hard",
        provider="example-provider",
        model="example-provider/hard-placeholder-1",
    )

    result = ApplicationResult(
        decision_id=decision.decision_id,
        session_id=decision.session_id,
        user_turn_id=decision.user_turn_id,
        session_revision=decision.session_revision,
        status=ApplicationStatus.REJECTED,
        applied=actual,
        failure_code="provider_rejected",
    )

    assert result.status is not ApplicationStatus.APPLIED
    assert result.applied == actual
    assert decision.target is not None
    assert result.applied != decision.target


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


def test_application_failed_requires_rejected_effective() -> None:
    with pytest.raises(ValidationError):
        RoutingRequest(
            request_id="r",
            integration_instance_id="i",
            session_id="s",
            user_turn_id="t",
            session_revision=1,
            event_kind="application_failed",
        )


def test_recovery_facts_require_failure_event() -> None:
    base = {
        "request_id": "r",
        "integration_instance_id": "i",
        "session_id": "s",
        "user_turn_id": "t",
        "session_revision": 1,
        "event_kind": "new_user_turn",
        "task_state": _state(),
    }
    with pytest.raises(ValidationError):
        RoutingRequest(**base, rejected_effective=_configuration())
    with pytest.raises(ValidationError):
        RoutingRequest(**base, recovery_used=True)
