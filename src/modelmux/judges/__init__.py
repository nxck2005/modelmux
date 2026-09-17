"""Task judge implementations."""

from __future__ import annotations

from modelmux.config import JudgeConfig, JudgeKind
from modelmux.judges.base import JudgeError, TaskJudge
from modelmux.judges.rules import RuleBasedTaskJudge
from modelmux.judges.static import StaticTaskJudge


def build_judge(config: JudgeConfig) -> TaskJudge:
    """Build the configured offline judge. Jev is not implemented yet."""
    if config.selection is JudgeKind.STATIC:
        if config.static is None:
            raise ValueError("static judge selection requires [judge.static.scores]")
        return StaticTaskJudge(config.static.scores)
    if config.selection is JudgeKind.RULES:
        return RuleBasedTaskJudge()
    raise ValueError("jev judge is not implemented")


__all__ = [
    "JudgeError",
    "RuleBasedTaskJudge",
    "StaticTaskJudge",
    "TaskJudge",
    "build_judge",
]
