"""Tests for the harness-independent routing core."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from modelmux.config import RouterConfig, load_config
from modelmux.judges import build_judge
from modelmux.judges.base import JudgeError
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
    JudgeResult,
    ObservationKind,
    TaskProfile,
    TaskState,
)

EXAMPLE_CONFIG = Path(__file__).resolve().parents[1] / "examples" / "router.toml"


class _FakeJudge:
    """Injected judge returning a fixed profile, raising, or sleeping."""

    def __init__(
        self,
        profile: TaskProfile | None = None,
        *,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.profile = profile
        self.error = error
        self.delay = delay
        self.calls = 0
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def evaluate(self, state: TaskState) -> JudgeResult:
        self.calls += 1
        self.started.set()
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.error is not None:
                raise self.error
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        assert self.profile is not None
        return JudgeResult(
            profile=self.profile,
            observation_kind=ObservationKind.PROBABILITY,
            judge_id="fake",
            question_version=QUESTION_VERSION,
        )


def _config(
    *, deadline_ms: int = 1500, hard_provider: str = "provider-a"
) -> RouterConfig:
    return RouterConfig(
        version=1,
        router={
            "default": "normal",
            "judgment_fallback": "normal",
            "compatibility_fallback": "hard",
            "economy": "cheap",
        },
        judge={"selection": "rules", "deadline_ms": deadline_ms},
        profiles={
            "cheap": {
                "provider": "provider-a",
                "model": "model-cheap",
                "rank": 0,
                "capabilities": ["text"],
            },
            "normal": {
                "provider": "provider-a",
                "model": "model-normal",
                "rank": 1,
                "capabilities": ["text", "tools"],
            },
            "hard": {
                "provider": hard_provider,
                "model": "model-hard",
                "rank": 2,
                "capabilities": ["text", "tools"],
            },
        },
        policy={
            "rules": [
                {
                    "id": "routine-cheap",
                    "route": "cheap",
                    "conditions": [
                        {"field": "routine", "op": "gte", "value": 0.90},
                        {"field": "reasoning_demand", "op": "lte", "value": 0.20},
                    ],
                },
                {
                    "id": "consequence-hard",
                    "route": "hard",
                    "conditions": [
                        {"field": "high_consequence", "op": "gte", "value": 0.70}
                    ],
                },
                {
                    "id": "reasoning-hard",
                    "route": "hard",
                    "conditions": [
                        {"field": "reasoning_demand", "op": "gte", "value": 0.65}
                    ],
                },
            ]
        },
    )


def _profile_values(**overrides: float) -> TaskProfile:
    scores = {
        "reasoning_demand": 0.3,
        "routine": 0.3,
        "underspecified": 0.3,
        "high_consequence": 0.1,
    }
    scores.update(overrides)
    return TaskProfile(**scores)


def _state(text: str = "do the thing") -> TaskState:
    return extract_task_state([MessageRecord(role="user", text=text)])


def _availability(
    *names: str, capabilities: frozenset[str] = frozenset({"text", "tools"})
) -> tuple[ProfileAvailability, ...]:
    return tuple(
        ProfileAvailability(profile_id=name, capabilities=capabilities)
        for name in names
    )


def _current(
    profile_id: str = "normal",
    *,
    provider: str = "provider-a",
    model: str = "model-normal",
) -> EffectiveConfiguration:
    return EffectiveConfiguration(profile_id=profile_id, provider=provider, model=model)


def _request(
    state: TaskState | None = None,
    *,
    current: EffectiveConfiguration | None = None,
    event_kind: EventKind = EventKind.NEW_USER_TURN,
    available: tuple[ProfileAvailability, ...] | None = None,
    required: frozenset[str] = frozenset(),
    safe: bool = True,
    history_portable: bool = True,
) -> RoutingRequest:
    return RoutingRequest(
        request_id="req-1",
        session_id="sess-1",
        user_turn_id="turn-1",
        session_revision=2,
        event_kind=event_kind,
        task_state=state if state is not None else _state(),
        current=current,
        available_profiles=(
            available
            if available is not None
            else _availability("cheap", "normal", "hard")
        ),
        required_capabilities=required,
        safe_to_apply_before_generation=safe,
        history_portable=history_portable,
    )


async def test_default_routing_echoes_correlation() -> None:
    judge = _FakeJudge(_profile_values())

    decision = await Router(_config(), judge).route(_request())

    assert decision.action is RouteAction.SWITCH
    assert decision.reason_code is ReasonCode.DEFAULT_PROFILE
    assert decision.target is not None
    assert decision.target.profile_id == "normal"
    assert decision.target.provider == "provider-a"
    assert decision.target.model == "model-normal"
    assert decision.request_id == "req-1"
    assert decision.session_id == "sess-1"
    assert decision.user_turn_id == "turn-1"
    assert decision.session_revision == 2
    assert decision.judge_id == "fake"
    assert decision.observation_kind is ObservationKind.PROBABILITY
    assert decision.question_version == QUESTION_VERSION
    assert decision.decision_id


async def test_rule_threshold_equality_matches() -> None:
    judge = _FakeJudge(_profile_values(high_consequence=0.70))

    decision = await Router(_config(), judge).route(_request())

    assert decision.action is RouteAction.SWITCH
    assert decision.reason_code is ReasonCode.RULE_MATCH
    assert decision.matched_rule_id == "consequence-hard"
    assert decision.target is not None
    assert decision.target.profile_id == "hard"


async def test_configured_available_intersection_uses_fallback() -> None:
    judge = _FakeJudge(_profile_values(routine=0.95, reasoning_demand=0.10))

    decision = await Router(_config(), judge).route(
        _request(available=_availability("normal", "hard"))
    )

    assert decision.action is RouteAction.SWITCH
    assert decision.reason_code is ReasonCode.COMPATIBILITY_FALLBACK
    assert decision.matched_rule_id == "routine-cheap"
    assert decision.target is not None
    assert decision.target.profile_id == "hard"


async def test_no_configured_available_profile_stops() -> None:
    judge = _FakeJudge(_profile_values())

    decision = await Router(_config(), judge).route(_request(available=_availability()))

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.NO_COMPATIBLE_PROFILE
    assert decision.target is None
    assert decision.continues_with_current is False


async def test_required_capability_excludes_candidate() -> None:
    judge = _FakeJudge(_profile_values(routine=0.95, reasoning_demand=0.10))

    decision = await Router(_config(), judge).route(
        _request(required=frozenset({"tools"}))
    )

    assert decision.action is RouteAction.SWITCH
    assert decision.reason_code is ReasonCode.COMPATIBILITY_FALLBACK
    assert decision.target is not None
    assert decision.target.profile_id == "hard"


async def test_incomplete_state_vetoes_economy() -> None:
    judge = _FakeJudge(_profile_values(routine=0.95, reasoning_demand=0.10))
    complete = _state()
    incomplete = complete.model_copy(update={"history_omitted": 1})

    vetoed = await Router(_config(), judge).route(_request(incomplete))
    routed = await Router(_config(), judge).route(_request(complete))

    assert vetoed.action is RouteAction.SWITCH
    assert vetoed.reason_code is ReasonCode.ECONOMY_VETOED
    assert vetoed.target is not None
    assert vetoed.target.profile_id == "normal"
    assert routed.target is not None
    assert routed.target.profile_id == "cheap"


async def test_escalation_switches_at_safe_boundary() -> None:
    judge = _FakeJudge(_profile_values(high_consequence=0.9))

    decision = await Router(_config(), judge).route(
        _request(current=_current("normal"))
    )

    assert decision.action is RouteAction.SWITCH
    assert decision.reason_code is ReasonCode.ESCALATED
    assert decision.target is not None
    assert decision.target.profile_id == "hard"


async def test_no_deescalation_keeps_current() -> None:
    judge = _FakeJudge(_profile_values(routine=0.95, reasoning_demand=0.10))
    current = _current("hard", model="model-hard")

    decision = await Router(_config(), judge).route(_request(current=current))

    assert decision.action is RouteAction.KEEP
    assert decision.reason_code is ReasonCode.RETAINED_CURRENT
    assert decision.target == current


async def test_unsafe_boundary_defers() -> None:
    judge = _FakeJudge(_profile_values(high_consequence=0.9))

    decision = await Router(_config(), judge).route(
        _request(current=_current("normal"), safe=False)
    )

    assert decision.action is RouteAction.DEFER
    assert decision.reason_code is ReasonCode.UNSAFE_BOUNDARY
    assert decision.target is None
    assert decision.continues_with_current is True
    assert decision.matched_rule_id == "consequence-hard"


async def test_cross_provider_escalation_with_bound_history_defers() -> None:
    judge = _FakeJudge(_profile_values(high_consequence=0.9))

    decision = await Router(_config(hard_provider="provider-b"), judge).route(
        _request(current=_current("normal"), history_portable=False)
    )

    assert decision.action is RouteAction.DEFER
    assert decision.reason_code is ReasonCode.INCOMPATIBLE_HISTORY


async def test_unavailable_escalation_candidate_defers() -> None:
    judge = _FakeJudge(_profile_values(high_consequence=0.9))

    decision = await Router(_config(), judge).route(
        _request(
            current=_current("normal"),
            available=_availability("cheap", "normal"),
        )
    )

    assert decision.action is RouteAction.DEFER
    assert decision.reason_code is ReasonCode.CANDIDATE_UNAVAILABLE
    assert decision.continues_with_current is True


async def test_invalid_current_uses_compatibility_fallback() -> None:
    judge = _FakeJudge(_profile_values())
    request = _request(
        current=_current("normal"),
        available=_availability("cheap", "hard"),
    )

    decision = await Router(_config(), judge).route(request)

    assert decision.action is RouteAction.SWITCH
    assert decision.reason_code is ReasonCode.COMPATIBILITY_FALLBACK
    assert decision.target is not None
    assert decision.target.profile_id == "hard"


async def test_tool_continuation_bypasses_judge() -> None:
    judge = _FakeJudge(_profile_values(high_consequence=0.9))
    current = _current("hard", model="model-hard")

    decision = await Router(_config(), judge).route(
        _request(current=current, event_kind=EventKind.TOOL_CONTINUATION)
    )

    assert decision.action is RouteAction.KEEP
    assert decision.reason_code is ReasonCode.TOOL_CONTINUATION
    assert decision.target == current
    assert judge.calls == 0


async def test_tool_continuation_rejects_unavailable_current() -> None:
    judge = _FakeJudge(_profile_values())

    decision = await Router(_config(), judge).route(
        _request(
            current=_current("normal"),
            event_kind=EventKind.TOOL_CONTINUATION,
            available=_availability("cheap"),
        )
    )

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.INVALID_CURRENT
    assert judge.calls == 0


async def test_judge_unavailable_retains_current() -> None:
    judge = _FakeJudge(error=JudgeError("unavailable"))
    current = _current("normal")

    decision = await Router(_config(), judge).route(_request(current=current))

    assert decision.action is RouteAction.KEEP
    assert decision.reason_code is ReasonCode.JUDGE_UNAVAILABLE
    assert decision.target == current


async def test_judge_unavailable_without_current_uses_judgment_fallback() -> None:
    judge = _FakeJudge(error=JudgeError("unavailable"))

    decision = await Router(_config(), judge).route(_request())

    assert decision.action is RouteAction.SWITCH
    assert decision.reason_code is ReasonCode.JUDGMENT_FALLBACK
    assert decision.target is not None
    assert decision.target.profile_id == "normal"


async def test_judge_timeout_retains_current_and_cancels_work() -> None:
    judge = _FakeJudge(_profile_values(), delay=10.0)
    current = _current("normal")

    decision = await Router(_config(deadline_ms=20), judge).route(
        _request(current=current)
    )

    assert decision.action is RouteAction.KEEP
    assert decision.reason_code is ReasonCode.JUDGE_TIMEOUT
    assert decision.target == current
    assert judge.cancelled.is_set()


async def test_common_programmer_error_is_not_swallowed() -> None:
    judge = _FakeJudge(error=RuntimeError("bug"))

    with pytest.raises(RuntimeError):
        await Router(_config(), judge).route(_request())


async def test_caller_cancellation_propagates() -> None:
    judge = _FakeJudge(_profile_values(), delay=10.0)
    task = asyncio.create_task(Router(_config(), judge).route(_request()))
    await judge.started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert judge.cancelled.is_set()


def test_request_requires_task_state_for_new_user_turn() -> None:
    with pytest.raises(ValidationError):
        RoutingRequest(
            request_id="r",
            session_id="s",
            user_turn_id="t",
            session_revision=1,
            event_kind=EventKind.NEW_USER_TURN,
        )


def test_request_requires_current_for_tool_continuation() -> None:
    with pytest.raises(ValidationError):
        RoutingRequest(
            request_id="r",
            session_id="s",
            user_turn_id="t",
            session_revision=1,
            event_kind=EventKind.TOOL_CONTINUATION,
        )


def test_request_rejects_duplicate_availability() -> None:
    with pytest.raises(ValidationError):
        RoutingRequest(
            request_id="r",
            session_id="s",
            user_turn_id="t",
            session_revision=1,
            event_kind=EventKind.NEW_USER_TURN,
            task_state=_state(),
            available_profiles=(
                ProfileAvailability(profile_id="normal"),
                ProfileAvailability(profile_id="normal"),
            ),
        )


async def test_end_to_end_offline_scenario() -> None:
    config = load_config(EXAMPLE_CONFIG)
    router = Router(config, build_judge(config.judge))
    request = RoutingRequest(
        request_id="req-e2e",
        session_id="sess-e2e",
        user_turn_id="turn-e2e",
        session_revision=1,
        event_kind=EventKind.NEW_USER_TURN,
        task_state=_state("rename the config key"),
        available_profiles=_availability("cheap", "normal", "hard"),
    )

    decision = await router.route(request)

    assert decision.action is RouteAction.SWITCH
    assert decision.reason_code is ReasonCode.RULE_MATCH
    assert decision.matched_rule_id == "routine-cheap"
    assert decision.target is not None
    assert decision.target.profile_id == "cheap"
    assert decision.target.provider == "example-provider"
    assert decision.target.model == "example-provider/cheap-placeholder-1"
    assert decision.target.reasoning_effort == "low"
    assert decision.target.output_limit == 2048
    assert decision.judge_id == "static"
    assert decision.observation_kind is ObservationKind.HEURISTIC
