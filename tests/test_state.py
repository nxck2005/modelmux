"""Tests for bounded task-state extraction."""

from __future__ import annotations

from modelmux.state import (
    HISTORY_CHARS,
    INSTRUCTIONS_CHARS,
    LATEST_USER_CHARS,
    TOOLS_CHARS,
    MessageRecord,
    ToolRecord,
    extract_task_state,
)


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
