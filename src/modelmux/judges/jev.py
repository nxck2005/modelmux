"""TypeSafe Jev judge adapter.

Verified against the official documentation on 2026-09-18:

- https://docs.typesafe.ai/introduction/quickstart
- https://docs.typesafe.ai/api
- https://docs.typesafe.ai/confidence

Endpoint ``POST https://api.typesafe.ai/v1/systemone`` with a bearer
credential. A Noul answer is ``{"type": "noul", "noul": <0..1>}`` and carries
no confidence property. TypeSafe-specific shapes stay inside this module.

The injected ``httpx.AsyncClient`` is owned by the caller and must be created
with ``trust_env=False`` so environment proxy and CA settings cannot intercept
the bearer key or task content; the constructor rejects a client that inherits
the environment. Missing or malformed credentials, transport or HTTP failures,
oversized responses, invalid JSON, and responses that do not match the
documented schema raise ``JudgeError``; the Router applies its normal fallback
behavior. No retries and no separate deadline are implemented here: the
Router's ``deadline_ms`` bounds the whole evaluation. Redirects are disabled
on the request itself, so an injected client configured to follow them cannot
forward task state to another host.
"""

from __future__ import annotations

import json
import os
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from modelmux.config import JevJudgeConfig
from modelmux.judges.base import JudgeError
from modelmux.types import (
    QUESTION_VERSION,
    Identifier,
    JudgeResult,
    JudgeUsage,
    NonNegativeInt,
    ObservationKind,
    Score,
    TaskProfile,
    TaskState,
)

_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
_JUDGE_ID = "jev"
MAX_RESPONSE_BYTES = 64 * 1024

_QUESTIONS: dict[str, dict[str, str]] = {
    "reasoning_demand": {
        "type": "noul",
        "instructions": (
            "Does completing this task require substantial multi-step inference, "
            "diagnosing interacting causes, or designing a non-obvious solution "
            "beyond executing a specified procedure?"
        ),
    },
    "routine": {
        "type": "noul",
        "instructions": (
            "Can this task be completed by applying an explicit, localized "
            "transformation or well-specified procedure without resolving "
            "substantive design choices?"
        ),
    },
    "underspecified": {
        "type": "noul",
        "instructions": (
            "Is decision-critical information absent or are requirements "
            "contradictory, so choosing a solution requires assumptions?"
        ),
    },
    "high_consequence": {
        "type": "noul",
        "instructions": (
            "Could an incorrect result plausibly cause serious data loss, "
            "security exposure, or disruptive production changes in the "
            "described setting?"
        ),
    },
}


class _NoulAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["noul"]
    noul: Score


class _Answers(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reasoning_demand: _NoulAnswer
    routine: _NoulAnswer
    underspecified: _NoulAnswer
    high_consequence: _NoulAnswer


class _Usage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None


class _Response(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: Identifier | None = None
    answers: _Answers
    usage: _Usage | None = None


class JevTaskJudge:
    """Evaluates task state with TypeSafe Jev over an injected HTTP client."""

    judge_id = _JUDGE_ID

    def __init__(self, config: JevJudgeConfig, client: httpx.AsyncClient) -> None:
        if client.trust_env:
            raise ValueError(
                "jev judge requires an httpx.AsyncClient created with trust_env=False"
            )
        self._config = config
        self._client = client
        key = os.environ.get(config.api_key_env)
        self._api_key = SecretStr(key) if key is not None else None

    async def evaluate(self, state: TaskState) -> JudgeResult:
        if self._api_key is None:
            raise JudgeError(
                f"credential environment variable {self._config.api_key_env!r} is not set"
            )
        api_key = self._api_key.get_secret_value()
        if not api_key.strip() or any(
            ord(char) < 0x20 or ord(char) == 0x7F for char in api_key
        ):
            raise JudgeError("configured credential is empty or malformed")
        try:
            async with self._client.stream(
                "POST",
                _ENDPOINT,
                headers={"Authorization": f"Bearer {api_key.strip()}"},
                json=_request_payload(self._config, state),
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise JudgeError("jev response exceeded the size limit")
                    body.extend(chunk)
        except httpx.HTTPError as exc:
            raise JudgeError(_http_error_message(exc)) from None
        try:
            payload = json.loads(body)
        except ValueError:
            raise JudgeError("jev response was not valid JSON") from None
        return _result(payload)


def _request_payload(config: JevJudgeConfig, state: TaskState) -> dict[str, object]:
    return {
        "state": _state_payload(config, state),
        "model": config.model,
        "questions": _QUESTIONS,
    }


def _state_payload(config: JevJudgeConfig, state: TaskState) -> dict[str, object]:
    payload: dict[str, object] = {}
    if state.latest_user is not None:
        payload["latest_user"] = state.latest_user.text
    for key, blocks, include in (
        ("instructions", state.instructions, config.include_instructions),
        ("history", state.history, config.include_history),
        ("tool_excerpts", state.tool_excerpts, config.include_tool_excerpts),
    ):
        if include and blocks:
            payload[key] = [
                {"source": block.source, "text": block.text} for block in blocks
            ]
    return payload


def _result(payload: object) -> JudgeResult:
    try:
        response = _Response.model_validate(payload)
    except ValidationError:
        raise JudgeError("jev response did not match the expected schema") from None
    answers = response.answers
    return JudgeResult(
        profile=TaskProfile(
            reasoning_demand=answers.reasoning_demand.noul,
            routine=answers.routine.noul,
            underspecified=answers.underspecified.noul,
            high_consequence=answers.high_consequence.noul,
        ),
        observation_kind=ObservationKind.PROBABILITY,
        judge_id=_JUDGE_ID,
        question_version=QUESTION_VERSION,
        model_id=response.model,
        usage=_usage(response.usage),
    )


def _usage(usage: _Usage | None) -> JudgeUsage | None:
    if usage is None:
        return None
    return JudgeUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )


def _http_error_message(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"jev request failed with status {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "jev request timed out"
    return "jev request failed"
