"""Offline tests for the TypeSafe Jev judge adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from modelmux.config import JevJudgeConfig, RouterConfig
from modelmux.judges import build_judge
from modelmux.judges.base import JudgeError
from modelmux.judges.jev import JevTaskJudge
from modelmux.router import (
    EffectiveConfiguration,
    EventKind,
    ProfileAvailability,
    ReasonCode,
    RouteAction,
    Router,
    RoutingRequest,
)
from modelmux.state import MessageRecord, extract_task_state
from modelmux.types import (
    QUESTION_VERSION,
    JudgeUsage,
    ObservationKind,
    TaskState,
)

_ENV = "MODELMUX_TEST_JEV_API_KEY"
_ENDPOINT = "https://api.typesafe.ai/v1/systemone"


def _state() -> TaskState:
    return extract_task_state(
        [
            MessageRecord(role="user", text="rename the config key"),
            MessageRecord(role="assistant", text="done"),
            MessageRecord(role="user", text="now diagnose the flaky test"),
        ]
    )


def _answer(value: object) -> dict[str, object]:
    return {"type": "noul", "noul": value}


def _answers(**overrides: object) -> dict[str, object]:
    answers: dict[str, object] = {
        "reasoning_demand": _answer(0.8),
        "routine": _answer(0.1),
        "underspecified": _answer(0.2),
        "high_consequence": _answer(0.3),
    }
    answers.update(overrides)
    return answers


def _response(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": "jev-latest",
        "answers": _answers(),
        "usage": {"input_tokens": 312, "output_tokens": 48},
    }
    payload.update(overrides)
    return payload


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _config(*, deadline_ms: int = 1500) -> RouterConfig:
    return RouterConfig(
        version=1,
        router={
            "default": "normal",
            "judgment_fallback": "normal",
            "compatibility_fallback": "normal",
            "economy": "normal",
        },
        judge={
            "selection": "jev",
            "deadline_ms": deadline_ms,
            "jev": {"api_key_env": _ENV},
        },
        profiles={
            "normal": {
                "provider": "provider-a",
                "model": "model-normal",
                "rank": 0,
                "capabilities": ["text"],
            }
        },
        policy={"rules": []},
    )


def _request(*, current: EffectiveConfiguration | None = None) -> RoutingRequest:
    return RoutingRequest(
        request_id="req-1",
        integration_instance_id="integration-1",
        session_id="sess-1",
        user_turn_id="turn-1",
        session_revision=1,
        event_kind=EventKind.NEW_USER_TURN,
        task_state=_state(),
        current=current,
        available_profiles=(ProfileAvailability(profile_id="normal"),),
    )


async def test_documented_request_and_answer_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "test-key")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_response())

    async with _client(handler) as client:
        judge = JevTaskJudge(
            JevJudgeConfig(api_key_env=_ENV, model="jev-latest"), client
        )
        result = await judge.evaluate(_state())

    assert result.profile.reasoning_demand == 0.8
    assert result.profile.routine == 0.1
    assert result.profile.underspecified == 0.2
    assert result.profile.high_consequence == 0.3
    assert result.observation_kind is ObservationKind.PROBABILITY
    assert result.judge_id == "jev"
    assert result.question_version == QUESTION_VERSION
    assert result.model_id == "jev-latest"
    assert result.usage == JudgeUsage(input_tokens=312, output_tokens=48)

    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == _ENDPOINT
    assert request.headers["authorization"] == "Bearer test-key"
    assert request.headers["content-type"] == "application/json"
    body = json.loads(request.content)
    assert body["model"] == "jev-latest"
    assert set(body["questions"]) == {
        "reasoning_demand",
        "routine",
        "underspecified",
        "high_consequence",
    }
    assert all(question["type"] == "noul" for question in body["questions"].values())
    assert body["state"]["latest_user"] == "now diagnose the flaky test"
    assert body["state"]["history"] == [
        {"source": "user", "text": "rename the config key"},
        {"source": "assistant", "text": "done"},
    ]


async def test_missing_credentials_raise_judge_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_response())

    async with _client(handler) as client:
        judge = JevTaskJudge(JevJudgeConfig(api_key_env=_ENV), client)
        with pytest.raises(JudgeError, match="not set"):
            await judge.evaluate(_state())

    assert calls == 0


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            _response(answers={k: v for k, v in _answers().items() if k != "routine"}),
            id="missing-answer",
        ),
        pytest.param(
            _response(answers=_answers(routine={"type": "choice", "noul": 0.1})),
            id="wrong-answer-type",
        ),
        pytest.param(
            _response(answers=_answers(routine=_answer(1.5))), id="out-of-range"
        ),
        pytest.param(_response(answers=_answers(routine=_answer(True))), id="boolean"),
        pytest.param(_response(answers=_answers(routine=_answer("0.5"))), id="string"),
        pytest.param(_response(answers=[]), id="answers-not-map"),
        pytest.param(_response(usage={"input_tokens": -1}), id="bad-usage"),
        pytest.param(_response(model=42), id="bad-model"),
        pytest.param([], id="payload-not-object"),
    ],
)
async def test_malformed_responses_raise_judge_error(
    monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    monkeypatch.setenv(_ENV, "test-key")

    async with _client(lambda request: httpx.Response(200, json=payload)) as client:
        judge = JevTaskJudge(JevJudgeConfig(api_key_env=_ENV), client)
        with pytest.raises(JudgeError, match="expected schema"):
            await judge.evaluate(_state())


@pytest.mark.parametrize("status", [401, 422, 429, 529])
async def test_http_status_errors_are_safe_judge_errors(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    monkeypatch.setenv(_ENV, "super-secret-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "raw-body-secret"})

    async with _client(handler) as client:
        judge = JevTaskJudge(JevJudgeConfig(api_key_env=_ENV), client)
        with pytest.raises(JudgeError) as excinfo:
            await judge.evaluate(_state())

    message = str(excinfo.value)
    assert str(status) in message
    assert "raw-body-secret" not in message
    assert "super-secret-key" not in message


async def test_connect_error_is_a_safe_judge_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection to internal-host refused")

    async with _client(handler) as client:
        judge = JevTaskJudge(JevJudgeConfig(api_key_env=_ENV), client)
        with pytest.raises(JudgeError) as excinfo:
            await judge.evaluate(_state())

    assert str(excinfo.value) == "jev request failed"


async def test_read_timeout_is_a_safe_judge_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out at internal-host")

    async with _client(handler) as client:
        judge = JevTaskJudge(JevJudgeConfig(api_key_env=_ENV), client)
        with pytest.raises(JudgeError, match="timed out"):
            await judge.evaluate(_state())


async def test_invalid_json_is_a_safe_judge_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<html>raw-response-secret</html>",
            headers={"content-type": "application/json"},
        )

    async with _client(handler) as client:
        judge = JevTaskJudge(JevJudgeConfig(api_key_env=_ENV), client)
        with pytest.raises(JudgeError) as excinfo:
            await judge.evaluate(_state())

    assert str(excinfo.value) == "jev response was not valid JSON"


async def test_absent_usage_and_model_stay_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "test-key")
    payload = _response()
    del payload["usage"]
    del payload["model"]

    async with _client(lambda request: httpx.Response(200, json=payload)) as client:
        judge = JevTaskJudge(JevJudgeConfig(api_key_env=_ENV), client)
        result = await judge.evaluate(_state())

    assert result.usage is None
    assert result.model_id is None


async def test_partial_usage_keeps_unknown_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "test-key")
    payload = _response(usage={"input_tokens": 10})

    async with _client(lambda request: httpx.Response(200, json=payload)) as client:
        judge = JevTaskJudge(JevJudgeConfig(api_key_env=_ENV), client)
        result = await judge.evaluate(_state())

    assert result.usage == JudgeUsage(input_tokens=10, output_tokens=None)


async def test_router_uses_judgment_fallback_when_credentials_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_response())

    async with _client(handler) as client:
        judge = JevTaskJudge(JevJudgeConfig(api_key_env=_ENV), client)
        decision = await Router(_config(), judge).route(_request())

    assert decision.action is RouteAction.SWITCH
    assert decision.reason_code is ReasonCode.JUDGMENT_FALLBACK
    assert decision.target is not None
    assert decision.target.profile_id == "normal"
    assert calls == 0


async def test_router_deadline_cancels_jev_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "test-key")
    cancelled = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return httpx.Response(200, json=_response())

    current = EffectiveConfiguration(
        profile_id="normal", provider="provider-a", model="model-normal"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        judge = JevTaskJudge(JevJudgeConfig(api_key_env=_ENV), client)
        decision = await Router(_config(deadline_ms=20), judge).route(
            _request(current=current)
        )

    assert decision.action is RouteAction.KEEP
    assert decision.reason_code is ReasonCode.JUDGE_TIMEOUT
    assert decision.target == current
    assert cancelled.is_set()


def test_build_judge_requires_client_for_jev() -> None:
    with pytest.raises(ValueError, match="injected"):
        build_judge(_config().judge)


async def test_build_judge_builds_jev_with_injected_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "test-key")

    async with _client(lambda request: httpx.Response(200, json=_response())) as client:
        judge = build_judge(_config().judge, client=client)

    assert isinstance(judge, JevTaskJudge)
