"""Task judge implementations."""

from __future__ import annotations

import httpx

from modelmux.config import JudgeConfig, JudgeKind
from modelmux.judges.base import JudgeError, TaskJudge
from modelmux.judges.jev import JevTaskJudge
from modelmux.judges.rules import RuleBasedTaskJudge
from modelmux.judges.static import StaticTaskJudge


def build_judge(
    config: JudgeConfig, *, client: httpx.AsyncClient | None = None
) -> TaskJudge:
    """Build the configured judge.

    Jev requires an injected async HTTP client owned by the caller; static and
    rules judges do not use one.
    """
    if config.selection is JudgeKind.STATIC:
        if config.static is None:
            raise ValueError("static judge selection requires [judge.static.scores]")
        return StaticTaskJudge(config.static.scores)
    if config.selection is JudgeKind.RULES:
        return RuleBasedTaskJudge()
    if config.jev is None:
        raise ValueError("jev judge selection requires [judge.jev]")
    if client is None:
        raise ValueError("jev judge selection requires an injected httpx.AsyncClient")
    return JevTaskJudge(config.jev, client)


__all__ = [
    "JudgeError",
    "JevTaskJudge",
    "RuleBasedTaskJudge",
    "StaticTaskJudge",
    "TaskJudge",
    "build_judge",
]
