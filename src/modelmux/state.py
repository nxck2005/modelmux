"""Deterministic extraction of bounded, harness-neutral task state."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from modelmux.types import BoundedText, Identifier, TaskState

LATEST_USER_CHARS = 6_000
INSTRUCTIONS_CHARS = 2_000
HISTORY_CHARS = 3_000
TOOLS_CHARS = 1_000

_TRUNCATION_MARKER = "\n[...truncated...]\n"
_USER_ROLE = "user"

_STRICT = ConfigDict(extra="forbid", frozen=True)


class MessageRecord(BaseModel):
    """One caller-supplied message or instruction block."""

    model_config = _STRICT

    role: Identifier
    text: str


class ToolRecord(BaseModel):
    """One caller-supplied tool description or schema excerpt."""

    model_config = _STRICT

    name: Identifier
    text: str


def extract_task_state(
    messages: Sequence[MessageRecord],
    *,
    instructions: Sequence[MessageRecord] = (),
    tools: Sequence[ToolRecord] = (),
) -> TaskState:
    """Build a bounded judge view without altering the supplied records.

    The latest message with role ``user`` becomes ``latest_user``; everything
    before it is the prior history. Categories are bounded independently and
    blocks that do not fit are truncated or omitted with explicit counts.
    """
    latest_index = _latest_user_index(messages)
    if latest_index is None:
        latest_user = None
        history_records = [(message.role, message.text) for message in messages]
    else:
        source, text = messages[latest_index].role, messages[latest_index].text
        bounded, omitted_chars = _truncate(text, LATEST_USER_CHARS)
        latest_user = BoundedText(
            source=source,
            text=bounded,
            truncated=omitted_chars > 0,
            omitted_chars=omitted_chars,
        )
        history_records = [
            (message.role, message.text) for message in messages[:latest_index]
        ]

    history, history_omitted = _select(
        history_records, HISTORY_CHARS, newest_first=True
    )
    instruction_blocks, instructions_omitted = _select(
        [(item.role, item.text) for item in instructions], INSTRUCTIONS_CHARS
    )
    tool_blocks, tools_omitted = _select(
        [(item.name, item.text) for item in tools], TOOLS_CHARS
    )

    provided_text = [message.text for message in messages]
    provided_text.extend(item.text for item in instructions)
    provided_text.extend(item.text for item in tools)

    return TaskState(
        latest_user=latest_user,
        instructions=instruction_blocks,
        history=history,
        tool_excerpts=tool_blocks,
        instructions_omitted=instructions_omitted,
        history_omitted=history_omitted,
        tools_omitted=tools_omitted,
        message_count=len(messages),
        input_bytes=sum(len(text.encode("utf-8")) for text in provided_text),
        tool_count=len(tools),
        tool_names=tuple(item.name for item in tools),
    )


def _latest_user_index(messages: Sequence[MessageRecord]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role == _USER_ROLE:
            return index
    return None


def _truncate(text: str, cap: int) -> tuple[str, int]:
    if len(text) <= cap:
        return text, 0
    available = cap - len(_TRUNCATION_MARKER)
    head = available // 2
    tail = available - head
    return text[:head] + _TRUNCATION_MARKER + text[len(text) - tail :], len(
        text
    ) - available


def _select(
    blocks: Sequence[tuple[str, str]], budget: int, *, newest_first: bool = False
) -> tuple[tuple[BoundedText, ...], int]:
    ordered = reversed(blocks) if newest_first else blocks
    selected: list[BoundedText] = []
    remaining = budget
    omitted = 0
    for source, text in ordered:
        if len(text) <= remaining:
            selected.append(
                BoundedText(source=source, text=text, truncated=False, omitted_chars=0)
            )
            remaining -= len(text)
        elif remaining >= len(_TRUNCATION_MARKER):
            bounded, omitted_chars = _truncate(text, remaining)
            selected.append(
                BoundedText(
                    source=source,
                    text=bounded,
                    truncated=True,
                    omitted_chars=omitted_chars,
                )
            )
            remaining = 0
        else:
            omitted += 1
    if newest_first:
        selected.reverse()
    return tuple(selected), omitted
