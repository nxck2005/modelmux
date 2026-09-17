"""Harness-independent routing over validated configuration and one judge."""

from __future__ import annotations

import asyncio
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, model_validator

from modelmux.config import ModelProfile, PositiveInt, RouterConfig
from modelmux.judges.base import JudgeError, TaskJudge
from modelmux.policy import match_rule
from modelmux.types import (
    QUESTION_VERSION,
    Identifier,
    JudgeResult,
    NonNegativeInt,
    ObservationKind,
    TaskState,
)

_STRICT = ConfigDict(extra="forbid", frozen=True)


class EventKind(StrEnum):
    """The caller event that triggered routing."""

    NEW_USER_TURN = "new_user_turn"
    TOOL_CONTINUATION = "tool_continuation"


class RouteAction(StrEnum):
    """The proposed routing action.

    ``keep`` and ``defer`` leave the current configuration in effect; ``switch``
    replaces it; ``stop`` means no valid configuration can execute.
    """

    KEEP = "keep"
    SWITCH = "switch"
    DEFER = "defer"
    STOP = "stop"


class ReasonCode(StrEnum):
    """Why a decision was made."""

    RULE_MATCH = "rule_match"
    DEFAULT_PROFILE = "default_profile"
    ECONOMY_VETOED = "economy_vetoed"
    RETAINED_CURRENT = "retained_current"
    ESCALATED = "escalated"
    COMPATIBILITY_FALLBACK = "compatibility_fallback"
    JUDGMENT_FALLBACK = "judgment_fallback"
    JUDGE_TIMEOUT = "judge_timeout"
    JUDGE_UNAVAILABLE = "judge_unavailable"
    TOOL_CONTINUATION = "tool_continuation"
    UNSAFE_BOUNDARY = "unsafe_boundary"
    INCOMPATIBLE_HISTORY = "incompatible_history"
    CANDIDATE_UNAVAILABLE = "candidate_unavailable"
    NO_COMPATIBLE_PROFILE = "no_compatible_profile"
    INVALID_CURRENT = "invalid_current"
    INCOMPLETE_STATE = "incomplete_state"


class EffectiveConfiguration(BaseModel):
    """A fully resolved profile/provider/model/effort/output selection."""

    model_config = _STRICT

    profile_id: Identifier
    provider: Identifier
    model: Identifier
    reasoning_effort: Identifier | None = None
    output_limit: PositiveInt | None = None


class ProfileAvailability(BaseModel):
    """Caller-reported availability and factual control support for a profile.

    ``reasoning_efforts``, ``max_output_tokens``, and ``context_limit`` are
    None when the caller does not know the fact.
    """

    model_config = _STRICT

    profile_id: Identifier
    capabilities: frozenset[Identifier] = frozenset()
    reasoning_efforts: frozenset[Identifier] | None = None
    max_output_tokens: PositiveInt | None = None
    context_limit: PositiveInt | None = None


class RoutingRequest(BaseModel):
    """A harness-neutral routing request.

    Correlation fields are echoed into the decision. A decision is a proposal
    only; the caller owns the actually applied configuration. ``None`` token
    or support facts are unknown, not zero.
    """

    model_config = _STRICT

    request_id: Identifier
    session_id: Identifier
    user_turn_id: Identifier
    session_revision: NonNegativeInt
    event_kind: EventKind
    task_state: TaskState | None = None
    current: EffectiveConfiguration | None = None
    available_profiles: tuple[ProfileAvailability, ...] = ()
    required_capabilities: frozenset[Identifier] = frozenset()
    required_output_tokens: PositiveInt | None = None
    current_input_tokens: NonNegativeInt | None = None
    safe_to_apply_before_generation: bool = True
    history_portable: bool = True

    @model_validator(mode="after")
    def _event_requirements(self) -> RoutingRequest:
        if self.event_kind is EventKind.NEW_USER_TURN and self.task_state is None:
            raise ValueError("new_user_turn requires task_state")
        if self.event_kind is EventKind.TOOL_CONTINUATION and self.current is None:
            raise ValueError("tool_continuation requires a current configuration")
        seen: set[str] = set()
        for available in self.available_profiles:
            if available.profile_id in seen:
                raise ValueError(
                    f"duplicate available profile {available.profile_id!r}"
                )
            seen.add(available.profile_id)
        return self


class RouteDecision(BaseModel):
    """A proposed routing action for the caller to apply."""

    model_config = _STRICT

    decision_id: Identifier
    request_id: Identifier
    session_id: Identifier
    user_turn_id: Identifier
    session_revision: NonNegativeInt
    action: RouteAction
    target: EffectiveConfiguration | None = None
    reason_code: ReasonCode
    matched_rule_id: Identifier | None = None
    judge_id: Identifier | None = None
    observation_kind: ObservationKind | None = None
    question_version: Identifier = QUESTION_VERSION

    @property
    def continues_with_current(self) -> bool:
        """Whether the caller may keep generating with the current route."""
        return self.action in (RouteAction.KEEP, RouteAction.DEFER)


class Router:
    """Deterministic routing over validated configuration and one judge."""

    def __init__(self, config: RouterConfig, judge: TaskJudge) -> None:
        self._config = config
        self._judge = judge

    async def route(self, request: RoutingRequest) -> RouteDecision:
        if request.event_kind is EventKind.TOOL_CONTINUATION:
            return self._continuation_decision(request)

        state = request.task_state
        assert state is not None
        try:
            async with asyncio.timeout(self._config.judge.deadline_ms / 1000):
                result = await self._judge.evaluate(state)
        except TimeoutError:
            return self._judge_failure_decision(
                request, state, ReasonCode.JUDGE_TIMEOUT
            )
        except JudgeError:
            return self._judge_failure_decision(
                request, state, ReasonCode.JUDGE_UNAVAILABLE
            )
        return self._turn_decision(request, state, result)

    def _turn_decision(
        self, request: RoutingRequest, state: TaskState, result: JudgeResult
    ) -> RouteDecision:
        rule = match_rule(result.profile, self._config.policy.rules)
        if rule is not None:
            candidate_id, reason, matched_rule_id = (
                rule.route,
                ReasonCode.RULE_MATCH,
                rule.id,
            )
        else:
            candidate_id, reason, matched_rule_id = (
                self._config.router.default,
                ReasonCode.DEFAULT_PROFILE,
                None,
            )

        if candidate_id == self._config.router.economy and _state_incomplete(state):
            candidate_id = self._config.router.default
            reason = ReasonCode.ECONOMY_VETOED
            if candidate_id == self._config.router.economy:
                return self._decision(
                    request,
                    RouteAction.STOP,
                    ReasonCode.INCOMPLETE_STATE,
                    result=result,
                )

        if request.current is None:
            return self._first_decision(
                request, state, candidate_id, reason, matched_rule_id, result
            )
        return self._active_decision(
            request, state, candidate_id, reason, matched_rule_id, result
        )

    def _first_decision(
        self,
        request: RoutingRequest,
        state: TaskState,
        candidate_id: str,
        reason: ReasonCode,
        matched_rule_id: str | None,
        result: JudgeResult,
    ) -> RouteDecision:
        if self._profile_compatible(candidate_id, request) is not None:
            if not request.safe_to_apply_before_generation:
                return self._decision(
                    request, RouteAction.STOP, ReasonCode.UNSAFE_BOUNDARY, result=result
                )
            return self._proposal(
                request, candidate_id, reason, matched_rule_id, result
            )

        fallback_id = self._config.router.compatibility_fallback
        if self._fallback_allowed(state, fallback_id) and self._profile_compatible(
            fallback_id, request
        ):
            if not request.safe_to_apply_before_generation:
                return self._decision(
                    request, RouteAction.STOP, ReasonCode.UNSAFE_BOUNDARY, result=result
                )
            return self._proposal(
                request,
                fallback_id,
                ReasonCode.COMPATIBILITY_FALLBACK,
                matched_rule_id,
                result,
            )
        return self._decision(
            request, RouteAction.STOP, ReasonCode.NO_COMPATIBLE_PROFILE, result=result
        )

    def _active_decision(
        self,
        request: RoutingRequest,
        state: TaskState,
        candidate_id: str,
        reason: ReasonCode,
        matched_rule_id: str | None,
        result: JudgeResult,
    ) -> RouteDecision:
        current = request.current
        assert current is not None
        if not self._current_valid(request):
            return self._recovery_decision(request, state, result)

        current_profile = self._config.profiles[current.profile_id]
        candidate_profile = self._config.profiles[candidate_id]
        if candidate_profile.rank <= current_profile.rank:
            return self._decision(
                request,
                RouteAction.KEEP,
                ReasonCode.RETAINED_CURRENT,
                target=current,
                matched_rule_id=matched_rule_id,
                result=result,
            )

        if self._profile_compatible(candidate_id, request) is not None:
            if not request.safe_to_apply_before_generation:
                return self._decision(
                    request,
                    RouteAction.DEFER,
                    ReasonCode.UNSAFE_BOUNDARY,
                    matched_rule_id=matched_rule_id,
                    result=result,
                )
            if not self._history_compatible(request, current, candidate_profile):
                return self._decision(
                    request,
                    RouteAction.DEFER,
                    ReasonCode.INCOMPATIBLE_HISTORY,
                    matched_rule_id=matched_rule_id,
                    result=result,
                )
            return self._proposal(
                request, candidate_id, ReasonCode.ESCALATED, matched_rule_id, result
            )

        fallback_id = self._config.router.compatibility_fallback
        fallback_profile = self._config.profiles.get(fallback_id)
        if (
            fallback_profile is not None
            and fallback_profile.rank > current_profile.rank
            and self._fallback_allowed(state, fallback_id)
            and self._profile_compatible(fallback_id, request) is not None
            and self._switch_safe(request, current, fallback_profile)
        ):
            return self._proposal(
                request,
                fallback_id,
                ReasonCode.COMPATIBILITY_FALLBACK,
                matched_rule_id,
                result,
            )
        return self._decision(
            request,
            RouteAction.DEFER,
            ReasonCode.CANDIDATE_UNAVAILABLE,
            matched_rule_id=matched_rule_id,
            result=result,
        )

    def _recovery_decision(
        self,
        request: RoutingRequest,
        state: TaskState,
        result: JudgeResult,
    ) -> RouteDecision:
        fallback_id = self._config.router.compatibility_fallback
        fallback_profile = self._config.profiles.get(fallback_id)
        if (
            fallback_profile is not None
            and self._fallback_allowed(state, fallback_id)
            and self._profile_compatible(fallback_id, request) is not None
        ):
            if not request.safe_to_apply_before_generation:
                return self._decision(
                    request, RouteAction.STOP, ReasonCode.UNSAFE_BOUNDARY, result=result
                )
            if not self._history_compatible(request, request.current, fallback_profile):
                return self._decision(
                    request,
                    RouteAction.STOP,
                    ReasonCode.INCOMPATIBLE_HISTORY,
                    result=result,
                )
            return self._proposal(
                request, fallback_id, ReasonCode.COMPATIBILITY_FALLBACK, None, result
            )
        return self._decision(
            request, RouteAction.STOP, ReasonCode.NO_COMPATIBLE_PROFILE, result=result
        )

    def _continuation_decision(self, request: RoutingRequest) -> RouteDecision:
        current = request.current
        assert current is not None
        if not self._current_valid(request):
            return self._decision(request, RouteAction.STOP, ReasonCode.INVALID_CURRENT)
        return self._decision(
            request,
            RouteAction.KEEP,
            ReasonCode.TOOL_CONTINUATION,
            target=current,
        )

    def _judge_failure_decision(
        self, request: RoutingRequest, state: TaskState, reason: ReasonCode
    ) -> RouteDecision:
        current = request.current
        if current is not None and self._current_valid(request):
            return self._decision(request, RouteAction.KEEP, reason, target=current)

        fallback_id = self._config.router.judgment_fallback
        fallback_profile = self._config.profiles.get(fallback_id)
        if (
            fallback_profile is not None
            and self._fallback_allowed(state, fallback_id)
            and self._profile_compatible(fallback_id, request) is not None
        ):
            if not request.safe_to_apply_before_generation:
                return self._decision(
                    request, RouteAction.STOP, ReasonCode.UNSAFE_BOUNDARY
                )
            if not self._history_compatible(request, current, fallback_profile):
                return self._decision(
                    request, RouteAction.STOP, ReasonCode.INCOMPATIBLE_HISTORY
                )
            return self._proposal(
                request, fallback_id, ReasonCode.JUDGMENT_FALLBACK, None, None
            )
        return self._decision(
            request, RouteAction.STOP, ReasonCode.NO_COMPATIBLE_PROFILE
        )

    def _current_valid(self, request: RoutingRequest) -> bool:
        current = request.current
        if current is None:
            return False
        profile = self._config.profiles.get(current.profile_id)
        if profile is None:
            return False
        availability = self._availability(current.profile_id, request)
        if availability is None:
            return False
        if current.provider != profile.provider or current.model != profile.model:
            return False
        if current.reasoning_effort != profile.reasoning_effort:
            return False
        return self._settings_compatible(
            profile, availability, request, output_limit=current.output_limit
        )

    def _profile_compatible(
        self, profile_id: str, request: RoutingRequest
    ) -> ModelProfile | None:
        profile = self._config.profiles.get(profile_id)
        if profile is None:
            return None
        availability = self._availability(profile_id, request)
        if availability is None:
            return None
        output_limit = _candidate_output_limit(profile, request)
        if not self._settings_compatible(
            profile, availability, request, output_limit=output_limit
        ):
            return None
        return profile

    @staticmethod
    def _availability(
        profile_id: str, request: RoutingRequest
    ) -> ProfileAvailability | None:
        return next(
            (
                available
                for available in request.available_profiles
                if available.profile_id == profile_id
            ),
            None,
        )

    @staticmethod
    def _settings_compatible(
        profile: ModelProfile,
        availability: ProfileAvailability,
        request: RoutingRequest,
        *,
        output_limit: int | None,
    ) -> bool:
        required = request.required_capabilities
        if not required <= availability.capabilities:
            return False
        if not required <= set(profile.capabilities):
            return False

        effort = profile.reasoning_effort
        if effort is not None and availability.reasoning_efforts is not None:
            if effort not in availability.reasoning_efforts:
                return False

        ceiling = _known_output_ceiling(profile, availability)
        required_output = request.required_output_tokens
        if output_limit is not None and ceiling is not None:
            if output_limit > ceiling:
                return False
        if required_output is not None and output_limit is not None:
            if output_limit < required_output:
                return False
        if required_output is not None and ceiling is not None:
            if required_output > ceiling:
                return False

        context_limit = _known_context_limit(profile, availability)
        if (
            context_limit is not None
            and request.current_input_tokens is not None
            and output_limit is not None
        ):
            if request.current_input_tokens + output_limit > context_limit:
                return False
        return True

    @staticmethod
    def _history_compatible(
        request: RoutingRequest,
        current: EffectiveConfiguration | None,
        target_profile: ModelProfile,
    ) -> bool:
        if current is None:
            return True
        return request.history_portable or target_profile.provider == current.provider

    def _switch_safe(
        self,
        request: RoutingRequest,
        current: EffectiveConfiguration | None,
        target_profile: ModelProfile,
    ) -> bool:
        if not request.safe_to_apply_before_generation:
            return False
        return self._history_compatible(request, current, target_profile)

    def _fallback_allowed(self, state: TaskState, fallback_id: str) -> bool:
        return not (
            fallback_id == self._config.router.economy and _state_incomplete(state)
        )

    def _proposal(
        self,
        request: RoutingRequest,
        profile_id: str,
        reason: ReasonCode,
        matched_rule_id: str | None,
        result: JudgeResult | None,
    ) -> RouteDecision:
        profile = self._config.profiles[profile_id]
        return self._decision(
            request,
            RouteAction.SWITCH,
            reason,
            target=_resolve(
                profile_id, profile, _candidate_output_limit(profile, request)
            ),
            matched_rule_id=matched_rule_id,
            result=result,
        )

    @staticmethod
    def _decision(
        request: RoutingRequest,
        action: RouteAction,
        reason: ReasonCode,
        *,
        target: EffectiveConfiguration | None = None,
        matched_rule_id: str | None = None,
        result: JudgeResult | None = None,
    ) -> RouteDecision:
        return RouteDecision(
            decision_id=uuid4().hex,
            request_id=request.request_id,
            session_id=request.session_id,
            user_turn_id=request.user_turn_id,
            session_revision=request.session_revision,
            action=action,
            target=target,
            reason_code=reason,
            matched_rule_id=matched_rule_id,
            judge_id=result.judge_id if result is not None else None,
            observation_kind=result.observation_kind if result is not None else None,
        )


def _candidate_output_limit(
    profile: ModelProfile, request: RoutingRequest
) -> int | None:
    default = profile.output.default if profile.output is not None else None
    required = request.required_output_tokens
    if required is None:
        return default
    if default is None:
        return required
    return max(default, required)


def _known_output_ceiling(
    profile: ModelProfile, availability: ProfileAvailability
) -> int | None:
    ceilings = [
        value
        for value in (
            profile.output.max if profile.output is not None else None,
            availability.max_output_tokens,
        )
        if value is not None
    ]
    return min(ceilings) if ceilings else None


def _known_context_limit(
    profile: ModelProfile, availability: ProfileAvailability
) -> int | None:
    limits = [
        value
        for value in (profile.context_limit, availability.context_limit)
        if value is not None
    ]
    return min(limits) if limits else None


def _resolve(
    profile_id: str, profile: ModelProfile, output_limit: int | None
) -> EffectiveConfiguration:
    return EffectiveConfiguration(
        profile_id=profile_id,
        provider=profile.provider,
        model=profile.model,
        reasoning_effort=profile.reasoning_effort,
        output_limit=output_limit,
    )


def _state_incomplete(state: TaskState) -> bool:
    if state.latest_user is None or state.latest_user.truncated:
        return True
    blocks = state.history + state.instructions + state.tool_excerpts
    if any(block.truncated for block in blocks):
        return True
    return bool(
        state.history_omitted or state.instructions_omitted or state.tools_omitted
    )
