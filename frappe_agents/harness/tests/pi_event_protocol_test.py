"""Focused contract tests for the canonical Pi-shaped event stream."""

# Vendored from huggingface/tau v0.3.9 (commit 420ae60089c1adb30d415e4dde99f7323d3f4afb).
# Copyright (c) 2026 Alejandro AO. MIT licensed — see ../LICENSE.upstream.
# Local edits are marked "frappe_agents patch:".

from pathlib import Path

import pytest
from fake_provider import FakeProvider
from frappe_agents.harness import (
    AgentEndEvent,
    AgentHarness,
    AgentHarnessConfig,
    AssistantMessage,
    MessageEndEvent,
    MessageUpdateEvent,
    TextContent,
    ToolCall,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolResultMessage,
)
from frappe_agents.harness.provider_events import (
    AssistantDoneEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)

HARNESS = Path(__file__).resolve().parent.parent


# frappe_agents patch: upstream asserted that tau_agent never imports tau_ai.
# Here the same rule is that the vendored core imports neither its upstream
# siblings nor frappe.
def test_harness_package_imports_neither_tau_ai_nor_frappe() -> None:
    for path in HARNESS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "tau_ai" not in source, path
        assert "import frappe\n" not in source, path
        assert "from frappe " not in source, path


# frappe_agents patch: upstream asserted tau_ai re-exports the canonical event
# classes. Here the test double must use them rather than define its own.
def test_test_double_uses_the_canonical_event_contract() -> None:
    from fake_provider import AssistantMessageEvent as used

    assert used is AssistantMessageEvent


@pytest.mark.anyio
async def test_text_stream_has_nested_updates_and_terminal_messages() -> None:
    empty = AssistantMessage(model="fake")
    hello = AssistantMessage(model="fake", content=[TextContent(text="hello")])
    provider = FakeProvider(
        [
            [
                AssistantStartEvent(partial=empty),
                TextStartEvent(content_index=0, partial=empty),
                TextDeltaEvent(content_index=0, delta="hello", partial=hello),
                TextEndEvent(content_index=0, content="hello", partial=hello),
                AssistantDoneEvent(reason="stop", message=hello),
            ]
        ]
    )
    harness = AgentHarness(AgentHarnessConfig(provider=provider, model="fake", system="test"))

    events = [event async for event in harness.prompt("hi")]

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "message_start",
        "message_update",
        "message_update",
        "message_update",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    updates = [event for event in events if isinstance(event, MessageUpdateEvent)]
    assert [event.assistant_message_event.type for event in updates] == [
        "text_start",
        "text_delta",
        "text_end",
    ]
    assert isinstance(events[-1], AgentEndEvent)
    assert events[-1].messages[-1] == hello


@pytest.mark.anyio
async def test_tool_result_gets_execution_and_message_lifecycle_events() -> None:
    call = ToolCall(id="call-1", name="missing", arguments={})
    partial = AssistantMessage(model="fake", content=[call])
    provider = FakeProvider(
        [
            [
                AssistantStartEvent(partial=AssistantMessage(model="fake")),
                ToolCallStartEvent(content_index=0, partial=partial),
                ToolCallEndEvent(content_index=0, tool_call=call, partial=partial),
                AssistantDoneEvent(reason="toolUse", message=partial),
            ],
            [
                AssistantStartEvent(partial=AssistantMessage(model="fake")),
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(model="fake", content=[TextContent(text="done")]),
                ),
            ],
        ]
    )
    harness = AgentHarness(AgentHarnessConfig(provider=provider, model="fake", system="test"))

    events = [event async for event in harness.prompt("use it")]

    start = next(event for event in events if isinstance(event, ToolExecutionStartEvent))
    end = next(event for event in events if isinstance(event, ToolExecutionEndEvent))
    result_end = next(
        event
        for event in events
        if isinstance(event, MessageEndEvent) and isinstance(event.message, ToolResultMessage)
    )
    assert start.tool_call_id == end.tool_call_id == result_end.message.tool_call_id
    assert end.is_error is True
