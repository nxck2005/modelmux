"""Tests for the harness-independent routing core."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from modelmux.config import OutputLimits, RouterConfig, load_config
from modelmux.judges import build_judge
from modelmux.judges.base import JudgeError
from modelmux.router import (
    EffectiveConfiguration,
    EventKind,
    ProfileAvailability,
    ReasonCode,
    RouteAction,
    RouteDecision,
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
    *,
    deadline_ms: int = 1500,
    hard_provider: str = "provider-a",
    normal_provider: str = "provider-a",
    hard_effort: str | None = None,
    normal_output: OutputLimits | None = None,
    hard_output: OutputLimits | None = None,
    normal_context_limit: int | None = None,
    compatibility_fallback: str = "hard",
    economy: str = "cheap",
) -> RouterConfig:
    return RouterConfig(
        version=1,
        router={
            "default": "normal",
            "judgment_fallback": "normal",
            "compatibility_fallback": compatibility_fallback,
            "economy": economy,
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
                "provider": normal_provider,
                "model": "model-normal",
                "rank": 1,
                "capabilities": ["text", "tools"],
                "output": normal_output,
                "context_limit": normal_context_limit,
            },
            "hard": {
                "provider": hard_provider,
                "model": "model-hard",
                "rank": 2,
                "capabilities": ["text", "tools"],
                "reasoning_effort": hard_effort,
                "output": hard_output,
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
    *names: str,
    capabilities: frozenset[str] = frozenset({"text", "tools"}),
    reasoning_efforts: frozenset[str] | None = None,
    max_output_tokens: int | None = None,
    context_limit: int | None = None,
) -> tuple[ProfileAvailability, ...]:
    return tuple(
        ProfileAvailability(
            profile_id=name,
            capabilities=capabilities,
            reasoning_efforts=reasoning_efforts,
            max_output_tokens=max_output_tokens,
            context_limit=context_limit,
        )
        for name in names
    )


def _current(
    profile_id: str = "normal",
    *,
    provider: str = "provider-a",
    model: str = "model-normal",
    effort: str | None = None,
    output_limit: int | None = None,
) -> EffectiveConfiguration:
    return EffectiveConfiguration(
        profile_id=profile_id,
        provider=provider,
        model=model,
        reasoning_effort=effort,
        output_limit=output_limit,
    )


def _request(
    state: TaskState | None = None,
    *,
    current: EffectiveConfiguration | None = None,
    event_kind: EventKind = EventKind.NEW_USER_TURN,
    available: tuple[ProfileAvailability, ...] | None = None,
    required: frozenset[str] = frozenset(),
    required_output_tokens: int | None = None,
    current_input_tokens: int | None = None,
    safe: bool = True,
    history_portable: bool = True,
    application_failure_code: str | None = None,
) -> RoutingRequest:
    return RoutingRequest(
        request_id="req-1",
        integration_instance_id="integration-1",
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
        required_output_tokens=required_output_tokens,
        current_input_tokens=current_input_tokens,
        safe_to_apply_before_generation=safe,
        history_portable=history_portable,
        application_failure_code=application_failure_code,
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
            integration_instance_id="i",
            session_id="s",
            user_turn_id="t",
            session_revision=1,
            event_kind=EventKind.NEW_USER_TURN,
        )


def test_request_requires_current_for_tool_continuation() -> None:
    with pytest.raises(ValidationError):
        RoutingRequest(
            request_id="r",
            integration_instance_id="i",
            session_id="s",
            user_turn_id="t",
            session_revision=1,
            event_kind=EventKind.TOOL_CONTINUATION,
        )


def test_request_rejects_duplicate_availability() -> None:
    with pytest.raises(ValidationError):
        RoutingRequest(
            request_id="r",
            integration_instance_id="i",
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
        integration_instance_id="integration-e2e",
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


async def test_recovery_fallback_respects_bound_history() -> None:
    judge = _FakeJudge(_profile_values())
    request = _request(
        current=_current("normal"),
        available=_availability("cheap", "hard"),
        history_portable=False,
    )

    decision = await Router(_config(hard_provider="provider-b"), judge).route(request)

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.INCOMPATIBLE_HISTORY
    assert decision.continues_with_current is False


async def test_judge_failure_fallback_respects_bound_history() -> None:
    judge = _FakeJudge(error=JudgeError("unavailable"))
    request = _request(
        current=_current("hard", model="model-hard"),
        available=_availability("cheap", "normal"),
        history_portable=False,
    )

    decision = await Router(_config(normal_provider="provider-b"), judge).route(request)

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.INCOMPATIBLE_HISTORY


async def test_recovery_fallback_stops_when_boundary_unsafe() -> None:
    judge = _FakeJudge(_profile_values())
    request = _request(
        current=_current("normal"),
        available=_availability("cheap", "hard"),
        safe=False,
    )

    decision = await Router(_config(), judge).route(request)

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.UNSAFE_BOUNDARY
    assert decision.continues_with_current is False


async def test_current_model_mismatch_uses_recovery_fallback() -> None:
    judge = _FakeJudge(_profile_values())

    decision = await Router(_config(), judge).route(
        _request(current=_current("normal", model="not-in-catalog"))
    )

    assert decision.action is RouteAction.SWITCH
    assert decision.reason_code is ReasonCode.COMPATIBILITY_FALLBACK
    assert decision.target is not None
    assert decision.target.profile_id == "hard"


async def test_current_model_mismatch_stops_tool_continuation() -> None:
    judge = _FakeJudge(_profile_values())
    request = _request(
        current=_current("normal", model="not-in-catalog"),
        event_kind=EventKind.TOOL_CONTINUATION,
    )

    decision = await Router(_config(), judge).route(request)

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.INVALID_CURRENT
    assert judge.calls == 0


async def test_current_effort_mismatch_stops_tool_continuation() -> None:
    judge = _FakeJudge(_profile_values())
    request = _request(
        current=_current("normal", effort="high"),
        event_kind=EventKind.TOOL_CONTINUATION,
    )

    decision = await Router(_config(), judge).route(request)

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.INVALID_CURRENT


async def test_current_output_within_ceiling_is_valid() -> None:
    judge = _FakeJudge(_profile_values())
    config = _config(normal_output=OutputLimits(default=1024, max=4096))
    request = _request(
        current=_current("normal", output_limit=3000),
        event_kind=EventKind.TOOL_CONTINUATION,
    )

    decision = await Router(config, judge).route(request)

    assert decision.action is RouteAction.KEEP
    assert decision.reason_code is ReasonCode.TOOL_CONTINUATION


async def test_current_output_above_ceiling_stops_tool_continuation() -> None:
    judge = _FakeJudge(_profile_values())
    config = _config(normal_output=OutputLimits(default=1024, max=4096))
    request = _request(
        current=_current("normal", output_limit=5000),
        event_kind=EventKind.TOOL_CONTINUATION,
    )

    decision = await Router(config, judge).route(request)

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.INVALID_CURRENT


async def test_current_output_below_explicit_requirement_stops() -> None:
    judge = _FakeJudge(_profile_values())
    config = _config(normal_output=OutputLimits(default=1024, max=4096))
    request = _request(
        current=_current("normal", output_limit=3000),
        event_kind=EventKind.TOOL_CONTINUATION,
        required_output_tokens=4000,
    )

    decision = await Router(config, judge).route(request)

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.INVALID_CURRENT


async def test_escalation_does_not_downgrade_via_fallback() -> None:
    judge = _FakeJudge(_profile_values(high_consequence=0.9))

    decision = await Router(_config(compatibility_fallback="cheap"), judge).route(
        _request(
            current=_current("normal"),
            available=_availability("cheap", "normal"),
        )
    )

    assert decision.action is RouteAction.DEFER
    assert decision.reason_code is ReasonCode.CANDIDATE_UNAVAILABLE
    assert decision.target is None
    assert decision.continues_with_current is True


async def test_escalation_does_not_switch_to_equal_rank_fallback() -> None:
    judge = _FakeJudge(_profile_values(high_consequence=0.9))
    config = _config(compatibility_fallback="normal")

    decision = await Router(config, judge).route(
        _request(
            current=_current("normal"),
            available=_availability("cheap", "normal"),
        )
    )

    assert decision.action is RouteAction.DEFER
    assert decision.reason_code is ReasonCode.CANDIDATE_UNAVAILABLE
    assert decision.continues_with_current is True


async def test_escalation_fallback_respects_bound_history() -> None:
    judge = _FakeJudge(_profile_values(high_consequence=0.9))
    config = _config(compatibility_fallback="normal", normal_provider="provider-b")
    request = _request(
        current=_current("cheap", model="model-cheap"),
        available=_availability("cheap", "normal"),
        history_portable=False,
    )

    decision = await Router(config, judge).route(request)

    assert decision.action is RouteAction.DEFER
    assert decision.reason_code is ReasonCode.CANDIDATE_UNAVAILABLE


async def test_invalid_current_recovers_with_lower_rank_fallback() -> None:
    judge = _FakeJudge(_profile_values())

    decision = await Router(_config(compatibility_fallback="cheap"), judge).route(
        _request(current=_current("normal"), available=_availability("cheap"))
    )

    assert decision.action is RouteAction.SWITCH
    assert decision.reason_code is ReasonCode.COMPATIBILITY_FALLBACK
    assert decision.target is not None
    assert decision.target.profile_id == "cheap"


async def test_incomplete_state_blocks_economy_compatibility_fallback() -> None:
    judge = _FakeJudge(_profile_values())
    incomplete = _state().model_copy(update={"history_omitted": 1})
    request = _request(incomplete, available=_availability("cheap", "hard"))

    decision = await Router(_config(compatibility_fallback="cheap"), judge).route(
        request
    )

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.NO_COMPATIBLE_PROFILE


async def test_incomplete_state_blocks_escalating_economy_fallback() -> None:
    judge = _FakeJudge(_profile_values(high_consequence=0.9))
    incomplete = _state().model_copy(update={"history_omitted": 1})
    config = _config(compatibility_fallback="normal", economy="normal")
    request = _request(
        incomplete,
        current=_current("cheap", model="model-cheap"),
        available=_availability("cheap", "normal"),
    )

    decision = await Router(config, judge).route(request)

    assert decision.action is RouteAction.DEFER
    assert decision.reason_code is ReasonCode.CANDIDATE_UNAVAILABLE
    assert decision.continues_with_current is True


async def test_judge_failure_keeps_applied_economy_route() -> None:
    judge = _FakeJudge(error=JudgeError("unavailable"))
    incomplete = _state().model_copy(update={"history_omitted": 1})
    current = _current("cheap", model="model-cheap")

    decision = await Router(_config(), judge).route(
        _request(incomplete, current=current)
    )

    assert decision.action is RouteAction.KEEP
    assert decision.reason_code is ReasonCode.JUDGE_UNAVAILABLE
    assert decision.target == current


async def test_unknown_effort_support_is_not_rejected() -> None:
    judge = _FakeJudge(_profile_values(high_consequence=0.9))

    decision = await Router(_config(hard_effort="high"), judge).route(_request())

    assert decision.action is RouteAction.SWITCH
    assert decision.target is not None
    assert decision.target.profile_id == "hard"


async def test_candidate_rejected_for_unsupported_effort() -> None:
    judge = _FakeJudge(_profile_values(high_consequence=0.9))
    request = _request(
        available=_availability(
            "cheap", "normal", "hard", reasoning_efforts=frozenset({"low"})
        )
    )

    decision = await Router(_config(hard_effort="high"), judge).route(request)

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.NO_COMPATIBLE_PROFILE


async def test_required_output_above_ceiling_rejects_candidate() -> None:
    judge = _FakeJudge(_profile_values(high_consequence=0.9))
    config = _config(hard_output=OutputLimits(default=2048, max=4096))

    decision = await Router(config, judge).route(_request(required_output_tokens=8000))

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.NO_COMPATIBLE_PROFILE


async def test_required_output_raises_effective_limit_within_ceiling() -> None:
    judge = _FakeJudge(_profile_values())
    config = _config(normal_output=OutputLimits(default=1024, max=4096))

    decision = await Router(config, judge).route(_request(required_output_tokens=3000))

    assert decision.action is RouteAction.SWITCH
    assert decision.reason_code is ReasonCode.DEFAULT_PROFILE
    assert decision.target is not None
    assert decision.target.output_limit == 3000


async def test_known_context_limit_rejects_candidate_that_would_overflow() -> None:
    judge = _FakeJudge(_profile_values())
    config = _config(
        normal_output=OutputLimits(default=2048, max=4096),
        normal_context_limit=10000,
    )
    request = _request(
        current_input_tokens=7000,
        available=_availability("normal", context_limit=8000),
    )

    decision = await Router(config, judge).route(request)

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.NO_COMPATIBLE_PROFILE


async def test_unknown_context_facts_do_not_reject_candidate() -> None:
    judge = _FakeJudge(_profile_values())
    config = _config(normal_output=OutputLimits(default=2048, max=4096))

    decision = await Router(config, judge).route(_request(current_input_tokens=7000))

    assert decision.action is RouteAction.SWITCH
    assert decision.reason_code is ReasonCode.DEFAULT_PROFILE


async def test_current_context_overflow_stops_tool_continuation() -> None:
    judge = _FakeJudge(_profile_values())
    config = _config(
        normal_output=OutputLimits(default=2048, max=4096),
        normal_context_limit=10000,
    )
    request = _request(
        current=_current("normal", output_limit=2048),
        event_kind=EventKind.TOOL_CONTINUATION,
        current_input_tokens=9000,
    )

    decision = await Router(config, judge).route(request)

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.INVALID_CURRENT


async def test_reported_output_ceiling_intersects_with_configuration() -> None:
    judge = _FakeJudge(_profile_values())
    config = _config(normal_output=OutputLimits(default=1024, max=8192))
    request = _request(
        current=_current("normal", output_limit=4096),
        event_kind=EventKind.TOOL_CONTINUATION,
        available=_availability("cheap", "normal", "hard", max_output_tokens=2048),
    )

    decision = await Router(config, judge).route(request)

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.INVALID_CURRENT


async def test_unknown_output_input_overflow_rejects_first_turn_candidate() -> None:
    judge = _FakeJudge(_profile_values())
    config = _config(normal_context_limit=8000)
    request = _request(
        current_input_tokens=9000,
        available=_availability("normal"),
    )

    decision = await Router(config, judge).route(request)

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.NO_COMPATIBLE_PROFILE


async def test_context_overflow_candidate_uses_compatible_fallback() -> None:
    judge = _FakeJudge(_profile_values())
    config = _config(normal_context_limit=8000)

    decision = await Router(config, judge).route(_request(current_input_tokens=9000))

    assert decision.action is RouteAction.SWITCH
    assert decision.reason_code is ReasonCode.COMPATIBILITY_FALLBACK
    assert decision.target is not None
    assert decision.target.profile_id == "hard"


async def test_unknown_output_input_overflow_stops_tool_continuation() -> None:
    judge = _FakeJudge(_profile_values())
    config = _config(normal_context_limit=8000)
    request = _request(
        current=_current("normal"),
        event_kind=EventKind.TOOL_CONTINUATION,
        current_input_tokens=9000,
    )

    decision = await Router(config, judge).route(request)

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.INVALID_CURRENT


async def test_unknown_output_input_plus_required_overflow_stops_tool_continuation() -> (
    None
):
    judge = _FakeJudge(_profile_values())
    config = _config(normal_context_limit=8000)
    request = _request(
        current=_current("normal"),
        event_kind=EventKind.TOOL_CONTINUATION,
        current_input_tokens=7500,
        required_output_tokens=1000,
    )

    decision = await Router(config, judge).route(request)

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.INVALID_CURRENT


async def test_unknown_output_with_known_input_that_fits_is_retained() -> None:
    judge = _FakeJudge(_profile_values())
    config = _config(normal_context_limit=8000)
    request = _request(
        current=_current("normal"),
        event_kind=EventKind.TOOL_CONTINUATION,
        current_input_tokens=7000,
    )

    decision = await Router(config, judge).route(request)

    assert decision.action is RouteAction.KEEP
    assert decision.reason_code is ReasonCode.TOOL_CONTINUATION


async def test_known_output_at_context_boundary_is_valid() -> None:
    judge = _FakeJudge(_profile_values())
    config = _config(normal_context_limit=8000)
    request = _request(
        current=_current("normal", output_limit=1000),
        event_kind=EventKind.TOOL_CONTINUATION,
        current_input_tokens=7000,
    )

    decision = await Router(config, judge).route(request)

    assert decision.action is RouteAction.KEEP
    assert decision.reason_code is ReasonCode.TOOL_CONTINUATION


async def test_first_turn_output_at_context_boundary_is_accepted() -> None:
    judge = _FakeJudge(_profile_values())
    config = _config(
        normal_output=OutputLimits(default=1000, max=2000),
        normal_context_limit=8000,
    )
    request = _request(current_input_tokens=7000, available=_availability("normal"))

    decision = await Router(config, judge).route(request)

    assert decision.action is RouteAction.SWITCH
    assert decision.reason_code is ReasonCode.DEFAULT_PROFILE


async def test_application_failure_retains_valid_current() -> None:
    judge = _FakeJudge(_profile_values(high_consequence=0.9))
    current = _current("hard", model="model-hard")
    request = _request(
        current=current,
        event_kind=EventKind.APPLICATION_FAILED,
        application_failure_code="provider_rejected",
    )

    decision = await Router(_config(), judge).route(request)

    assert decision.action is RouteAction.KEEP
    assert decision.reason_code is ReasonCode.APPLICATION_FAILED
    assert decision.target == current
    assert decision.notify_on_applied is False
    assert judge.calls == 0


async def test_application_failure_falls_back_when_current_is_unusable() -> None:
    judge = _FakeJudge(_profile_values())
    request = _request(
        current=_current("normal"),
        event_kind=EventKind.APPLICATION_FAILED,
        available=_availability("cheap", "hard"),
    )

    decision = await Router(_config(), judge).route(request)

    assert decision.action is RouteAction.SWITCH
    assert decision.reason_code is ReasonCode.COMPATIBILITY_FALLBACK
    assert decision.target is not None
    assert decision.target.profile_id == "hard"
    assert judge.calls == 0


async def test_application_failure_without_state_vetoes_economy_fallback() -> None:
    judge = _FakeJudge(_profile_values())
    request = RoutingRequest(
        request_id="req-1",
        integration_instance_id="integration-1",
        session_id="sess-1",
        user_turn_id="turn-1",
        session_revision=2,
        event_kind=EventKind.APPLICATION_FAILED,
        current=_current("normal"),
        available_profiles=_availability("cheap"),
    )

    decision = await Router(_config(compatibility_fallback="cheap"), judge).route(
        request
    )

    assert decision.action is RouteAction.STOP
    assert decision.reason_code is ReasonCode.NO_COMPATIBLE_PROFILE


async def test_serialized_offline_library_flow() -> None:
    judge = _FakeJudge(_profile_values())
    router = Router(_config(), judge)

    first_request = _request()
    first = await router.route(
        RoutingRequest.model_validate_json(first_request.model_dump_json())
    )

    assert first.action is RouteAction.SWITCH
    assert first.reason_code is ReasonCode.DEFAULT_PROFILE
    assert first.target is not None
    assert first.target.profile_id == "normal"
    assert first.apply_at == "before_next_generation"
    assert first.config_digest is not None
    assert first.notify_on_applied is True
    assert first.elapsed_ms is not None
    assert "do the thing" not in first.model_dump_json()
    assert RouteDecision.model_validate_json(first.model_dump_json()) == first

    judge.profile = _profile_values(high_consequence=0.9)
    second = await router.route(
        RoutingRequest.model_validate_json(
            _request(current=first.target).model_dump_json()
        )
    )

    assert second.action is RouteAction.SWITCH
    assert second.reason_code is ReasonCode.ESCALATED
    assert second.target is not None
    assert second.target.profile_id == "hard"
    assert second.config_digest != first.config_digest

    calls = judge.calls
    third = await router.route(
        RoutingRequest.model_validate_json(
            _request(
                current=second.target, event_kind=EventKind.TOOL_CONTINUATION
            ).model_dump_json()
        )
    )

    assert third.action is RouteAction.KEEP
    assert third.reason_code is ReasonCode.TOOL_CONTINUATION
    assert third.target == second.target
    assert third.config_digest == second.config_digest
    assert judge.calls == calls

    judge.error = JudgeError("unavailable")
    fourth = await router.route(
        RoutingRequest.model_validate_json(
            _request(current=third.target).model_dump_json()
        )
    )

    assert fourth.action is RouteAction.KEEP
    assert fourth.reason_code is ReasonCode.JUDGE_UNAVAILABLE
    assert fourth.target == second.target
