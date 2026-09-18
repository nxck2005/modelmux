"""Tests for bounded task-state extraction."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from modelmux.state import (
    HISTORY_CHARS,
    INSTRUCTIONS_CHARS,
    LATEST_USER_CHARS,
    MAX_HISTORY_RECORDS,
    MAX_INSTRUCTION_RECORDS,
    MAX_SOURCE_CHARS,
    MAX_TOOL_NAMES,
    MAX_TOOL_RECORDS,
    TOOLS_CHARS,
    MessageRecord,
    ToolRecord,
    extract_task_state,
)
from modelmux.types import MAX_STATE_BYTES, BoundedText, TaskState


def _block(
    text: str = "",
    source: str = "user",
    *,
    source_truncated: bool = False,
    truncated: bool = False,
    omitted: int = 0,
) -> BoundedText:
    return BoundedText(
        source=source,
        source_truncated=source_truncated,
        text=text,
        truncated=truncated,
        omitted_chars=omitted,
    )


def _state(**overrides: object) -> TaskState:
    values: dict[str, object] = {
        "latest_user": None,
        "instructions": (),
        "history": (),
        "tool_excerpts": (),
        "instructions_omitted": 0,
        "history_omitted": 0,
        "tools_omitted": 0,
        "message_count": 0,
        "input_bytes": 0,
        "tool_count": 0,
        "tool_names": (),
        "tool_names_omitted": 0,
        "tool_names_truncated": 0,
    }
    values.update(overrides)
    return TaskState(**values)


def test_direct_state_rejects_oversized_text() -> None:
    with pytest.raises(ValidationError):
        _state(latest_user=_block("x" * (LATEST_USER_CHARS + 1)))
    with pytest.raises(ValidationError):
        _state(instructions=(_block("x" * (INSTRUCTIONS_CHARS + 1)),))
    with pytest.raises(ValidationError):
        _state(history=(_block("x" * (HISTORY_CHARS + 1)),))
    with pytest.raises(ValidationError):
        _state(tool_excerpts=(_block("x" * (TOOLS_CHARS + 1)),))


def test_directly_deserialized_state_rejects_oversized_text() -> None:
    payload = json.loads(_state().model_dump_json())
    payload["history"] = [
        {
            "source": "user",
            "source_truncated": False,
            "text": "x" * (HISTORY_CHARS + 1),
            "truncated": False,
            "omitted_chars": 0,
        }
    ]

    with pytest.raises(ValidationError):
        TaskState.model_validate(payload)


def test_direct_state_rejects_record_count_over_limit() -> None:
    with pytest.raises(ValidationError):
        _state(history=tuple(_block() for _ in range(MAX_HISTORY_RECORDS + 1)))
    with pytest.raises(ValidationError):
        _state(instructions=tuple(_block() for _ in range(MAX_INSTRUCTION_RECORDS + 1)))
    with pytest.raises(ValidationError):
        _state(tool_excerpts=tuple(_block() for _ in range(MAX_TOOL_RECORDS + 1)))
    with pytest.raises(ValidationError):
        _state(tool_names=tuple(f"t{index}" for index in range(MAX_TOOL_NAMES + 1)))


def test_direct_state_rejects_long_source_labels() -> None:
    with pytest.raises(ValidationError):
        _block("text", source="s" * (MAX_SOURCE_CHARS + 1))
    with pytest.raises(ValidationError):
        _state(tool_names=("t" * (MAX_SOURCE_CHARS + 1),))


def test_direct_state_rejects_excessive_serialized_overhead() -> None:
    with pytest.raises(ValidationError, match=f"exceeds {MAX_STATE_BYTES}"):
        _state(input_bytes=10 ** (MAX_STATE_BYTES * 2))


def test_direct_state_rejects_inconsistent_truncation_metadata() -> None:
    with pytest.raises(ValidationError):
        _state(latest_user=_block("text", truncated=True, omitted=0))


def test_extraction_bounds_many_empty_records() -> None:
    messages = [
        MessageRecord(role="assistant", text="")
        for _ in range(MAX_HISTORY_RECORDS + 10)
    ]
    messages.append(MessageRecord(role="user", text="latest"))

    state = extract_task_state(messages)

    assert len(state.history) == MAX_HISTORY_RECORDS
    assert state.history_omitted == 10
    assert state.message_count == MAX_HISTORY_RECORDS + 11


def test_extraction_bounds_many_instructions_and_tools() -> None:
    instructions = [
        MessageRecord(role="system", text="")
        for _ in range(MAX_INSTRUCTION_RECORDS + 5)
    ]
    tools = [
        ToolRecord(name=f"tool-{index}", text="")
        for index in range(MAX_TOOL_RECORDS + 5)
    ]

    state = extract_task_state([], instructions=instructions, tools=tools)

    assert len(state.instructions) == MAX_INSTRUCTION_RECORDS
    assert state.instructions_omitted == 5
    assert len(state.tool_excerpts) == MAX_TOOL_RECORDS
    assert state.tools_omitted == 5
    assert state.tool_count == MAX_TOOL_RECORDS + 5
    assert len(state.tool_names) == MAX_TOOL_NAMES
    assert state.tool_names_omitted == MAX_TOOL_RECORDS + 5 - MAX_TOOL_NAMES


def test_extraction_truncates_long_source_labels() -> None:
    messages = [
        MessageRecord(role="a" * 200, text="context"),
        MessageRecord(role="user", text="latest"),
    ]

    state = extract_task_state(
        messages,
        instructions=[MessageRecord(role="i" * 200, text="instruction")],
        tools=[ToolRecord(name="t" * 200, text="description")],
    )

    assert state.latest_user is not None
    assert state.latest_user.source == "user"
    assert state.latest_user.source_truncated is False
    assert state.history[0].source == "a" * MAX_SOURCE_CHARS
    assert state.history[0].source_truncated is True
    assert state.instructions[0].source == "i" * MAX_SOURCE_CHARS
    assert state.instructions[0].source_truncated is True
    assert state.tool_excerpts[0].source == "t" * MAX_SOURCE_CHARS
    assert state.tool_excerpts[0].source_truncated is True
    assert state.tool_names == ("t" * MAX_SOURCE_CHARS,)
    assert state.tool_names_truncated == 1


def test_extraction_counts_only_retained_truncated_tool_names() -> None:
    prefix = "t" * MAX_SOURCE_CHARS
    tools = [
        ToolRecord(name=f"{prefix}-{index}", text="")
        for index in range(MAX_TOOL_NAMES + 3)
    ]

    state = extract_task_state([], tools=tools)

    assert state.tool_names == (prefix,) * MAX_TOOL_NAMES
    assert state.tool_names_truncated == MAX_TOOL_NAMES
    assert state.tool_names_omitted == 3


def test_normal_complete_input_revalidates_unchanged() -> None:
    messages = [
        MessageRecord(role="user", text="hello"),
        MessageRecord(role="assistant", text="hi"),
        MessageRecord(role="user", text="latest"),
    ]

    state = extract_task_state(messages)

    assert [block.text for block in state.history] == ["hello", "hi"]
    assert all(block.source_truncated is False for block in state.history)
    assert state.history_omitted == 0
    assert state.tool_names_omitted == 0
    assert state.tool_names_truncated == 0
    assert TaskState.model_validate_json(state.model_dump_json()) == state


def test_extracts_latest_user_history_and_exact_counts() -> None:
    messages = [
        MessageRecord(role="user", text="first"),
        MessageRecord(role="assistant", text="reply"),
        MessageRecord(role="tool", text="output"),
        MessageRecord(role="user", text="latest"),
    ]

    state = extract_task_state(messages)

    assert state.latest_user is not None
    assert state.latest_user.text == "latest"
    assert state.message_count == 4
    assert state.input_bytes == sum(len(m.text.encode("utf-8")) for m in messages)
    assert [(block.source, block.text) for block in state.history] == [
        ("user", "first"),
        ("assistant", "reply"),
        ("tool", "output"),
    ]
    assert state.instructions == ()
    assert state.tool_count == 0


def test_preserves_supplied_records() -> None:
    messages = [
        MessageRecord(role="user", text="hello"),
        MessageRecord(role="assistant", text="hi"),
    ]
    instructions = [MessageRecord(role="system", text="be brief")]
    tools = [ToolRecord(name="bash", text="run commands")]
    before = (
        [message.model_dump() for message in messages],
        [item.model_dump() for item in instructions],
        [item.model_dump() for item in tools],
    )

    extract_task_state(messages, instructions=instructions, tools=tools)

    after = (
        [message.model_dump() for message in messages],
        [item.model_dump() for item in instructions],
        [item.model_dump() for item in tools],
    )
    assert after == before


def test_truncates_latest_user_with_head_and_tail() -> None:
    marker = "\n[...truncated...]\n"
    text = "A" * 4000 + "B" * 3000

    state = extract_task_state([MessageRecord(role="user", text=text)])

    assert state.latest_user is not None
    block = state.latest_user
    assert block.truncated is True
    assert len(block.text) <= LATEST_USER_CHARS
    assert block.text.count(marker) == 1
    retained_original = len(block.text) - len(marker)
    assert block.omitted_chars == len(text) - retained_original
    assert block.text.startswith("A" * 100)
    assert block.text.endswith("B" * 100)


def test_keeps_most_recent_history_and_marks_omissions() -> None:
    messages = [
        MessageRecord(
            role="assistant" if index % 2 == 0 else "tool",
            text=f"{index:04d}" + "x" * 496,
        )
        for index in range(10)
    ]
    messages.append(MessageRecord(role="user", text="latest"))

    state = extract_task_state(messages)

    assert state.history_omitted == 4
    assert [block.text[:4] for block in state.history] == [
        "0004",
        "0005",
        "0006",
        "0007",
        "0008",
        "0009",
    ]
    assert [block.source for block in state.history] == [
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    assert sum(len(block.text) for block in state.history) <= HISTORY_CHARS


def test_bounds_instructions_and_tools_and_records_omissions() -> None:
    instructions = [
        MessageRecord(role="system", text="i" * 1500),
        MessageRecord(role="developer", text="j" * 1500),
        MessageRecord(role="system", text="k" * 10),
    ]
    tools = [
        ToolRecord(name="bash", text="b" * 800),
        ToolRecord(name="read", text="r" * 800),
        ToolRecord(name="grep", text="g" * 10),
    ]

    state = extract_task_state(
        [MessageRecord(role="user", text="go")],
        instructions=instructions,
        tools=tools,
    )

    assert state.instructions_omitted == 1
    assert len(state.instructions) == 2
    assert state.instructions[0].truncated is False
    assert state.instructions[1].truncated is True
    assert sum(len(block.text) for block in state.instructions) <= INSTRUCTIONS_CHARS

    assert state.tool_count == 3
    assert state.tool_names == ("bash", "read", "grep")
    assert state.tools_omitted == 1
    assert state.tool_excerpts[0].text == "b" * 800
    assert state.tool_excerpts[1].truncated is True
    assert sum(len(block.text) for block in state.tool_excerpts) <= TOOLS_CHARS

    expected_bytes = sum(
        len(text.encode("utf-8"))
        for text in (
            ["go"]
            + [item.text for item in instructions]
            + [item.text for item in tools]
        )
    )
    assert state.input_bytes == expected_bytes


def test_total_selected_text_stays_within_plan_budget() -> None:
    messages = [MessageRecord(role="assistant", text="a" * 5000) for _ in range(3)]
    messages.append(MessageRecord(role="user", text="u" * 7000))

    state = extract_task_state(
        messages,
        instructions=[MessageRecord(role="system", text="i" * 5000)],
        tools=[ToolRecord(name="big", text="t" * 5000)],
    )

    selected = len(state.latest_user.text) if state.latest_user else 0
    selected += sum(len(block.text) for block in state.instructions)
    selected += sum(len(block.text) for block in state.history)
    selected += sum(len(block.text) for block in state.tool_excerpts)
    assert selected <= 12_000


def test_marks_missing_latest_user() -> None:
    state = extract_task_state([MessageRecord(role="assistant", text="orphan")])
    assert state.latest_user is None
    assert [block.text for block in state.history] == ["orphan"]
