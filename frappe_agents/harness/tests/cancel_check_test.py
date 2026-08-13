"""Tests for the frappe_agents cancel-check patch in the vendored loop."""

import pytest
from fake_provider import FakeProvider
from frappe_agents.harness import (
    AgentEndEvent,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    SimpleCancellationToken,
    TextContent,
    ToolCall,
    UserMessage,
)
from frappe_agents.harness.loop import run_agent_loop
from pi_event_helpers import assistant_done, assistant_start, tool_call_end


def _tool(name: str, execute_fn) -> AgentTool:
    return AgentTool(
        name=name,
        label=name.title(),
        description=f"Run {name}.",
        parameters={"type": "object"},
        execute_fn=execute_fn,
    )


@pytest.mark.anyio
async def test_loop_stops_before_the_first_model_call_when_already_cancelled() -> None:
    provider = FakeProvider([[assistant_start(), assistant_done(AssistantMessage(content="hi"))]])
    signal = SimpleCancellationToken()
    signal.cancel()
    messages: list[AgentMessage] = [UserMessage(content="hello")]

    events = [
        event
        async for event in run_agent_loop(
            provider=provider,
            model="fake",
            system="test",
            messages=messages,
            tools=[],
            signal=signal,
        )
    ]

    assert provider.calls == []
    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    error = messages[-1]
    assert isinstance(error, AssistantMessage)
    assert error.stop_reason == "error"
    assert error.error_message == "Operation aborted"


@pytest.mark.anyio
async def test_loop_stops_between_turns_when_cancelled_during_a_tool_call() -> None:
    signal = SimpleCancellationToken()

    async def execute(tool_call_id, arguments, tool_signal=None, on_update=None) -> AgentToolResult:
        del tool_call_id, arguments, tool_signal, on_update
        signal.cancel()
        return AgentToolResult(content=[TextContent(text="killed")])

    call = ToolCall(id="call-1", name="work", arguments={})
    first = AssistantMessage(content=[call], model="fake")
    second = AssistantMessage(content="never sent", model="fake")
    provider = FakeProvider(
        [
            [assistant_start(), tool_call_end(call), assistant_done(first, "toolUse")],
            [assistant_start(), assistant_done(second)],
        ]
    )
    messages: list[AgentMessage] = [UserMessage(content="work")]

    events = [
        event
        async for event in run_agent_loop(
            provider=provider,
            model="fake",
            system="test",
            messages=messages,
            tools=[_tool("work", execute)],
            signal=signal,
        )
    ]

    # The second turn must not reach the model.
    assert len(provider.calls) == 1
    end = events[-1]
    assert isinstance(end, AgentEndEvent)
    error = messages[-1]
    assert isinstance(error, AssistantMessage)
    assert error.stop_reason == "error"
    assert error.error_message == "Operation aborted"


@pytest.mark.anyio
async def test_loop_runs_normally_while_the_signal_stays_clear() -> None:
    provider = FakeProvider(
        [[assistant_start(), assistant_done(AssistantMessage(content="hi", model="fake"))]]
    )
    signal = SimpleCancellationToken()
    messages: list[AgentMessage] = [UserMessage(content="hello")]

    async for _event in run_agent_loop(
        provider=provider,
        model="fake",
        system="test",
        messages=messages,
        tools=[],
        signal=signal,
    ):
        pass

    assert len(provider.calls) == 1
    assistant = messages[-1]
    assert isinstance(assistant, AssistantMessage)
    assert assistant.stop_reason != "error"
