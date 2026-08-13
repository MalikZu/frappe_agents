"""Portable Pi-compatible agent harness primitives for Tau."""

# Vendored from huggingface/tau v0.3.9 (commit 420ae60089c1adb30d415e4dde99f7323d3f4afb).
# Copyright (c) 2026 Alejandro AO. MIT licensed — see LICENSE.upstream.
# Local edits are marked "frappe_agents patch:".

# ruff: noqa: F401 - this module intentionally defines the public facade

from frappe_agents.harness.events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from frappe_agents.harness.harness import (
    AgentHarness,
    AgentHarnessConfig,
    EventListener,
    QueuedMessages,
    SimpleCancellationToken,
)
from frappe_agents.harness.loop import run_agent_loop
from frappe_agents.harness.messages import (
    AgentMessage,
    AssistantMessage,
    BashExecutionMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UsageCost,
    UserMessage,
    content_text,
    message_text,
)

# frappe_agents patch: upstream's session/ package is not vendored — session
# state lives in Frappe doctypes, so its re-exports are dropped here.
from frappe_agents.harness.tools import (
    AgentTool,
    AgentToolResult,
    ToolCancellationToken,
    ToolExecutionMode,
    ToolExecutor,
    ToolUpdateCallback,
)
from frappe_agents.harness.types import JSONObject, JSONPrimitive, JSONValue

__all__ = [name for name in globals() if not name.startswith("_")]
