"""Provider contract owned by Tau's portable agent layer."""

# Vendored from huggingface/tau v0.3.9 (commit 420ae60089c1adb30d415e4dde99f7323d3f4afb).
# Copyright (c) 2026 Alejandro AO. MIT licensed — see LICENSE.upstream.
# Local edits are marked "frappe_agents patch:".

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from frappe_agents.harness.messages import AgentMessage
from frappe_agents.harness.provider_events import AssistantMessageEvent
from frappe_agents.harness.tools import AgentTool


class CancellationToken(Protocol):
    def is_cancelled(self) -> bool:
        """Return whether the current stream should stop."""
        ...


class ModelProvider(Protocol):
    """Provider-neutral Pi-compatible model stream interface."""

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        """Stream one model response as assistant message events.

        Providers may use ``session_id`` for request routing or prompt-cache
        affinity. Unsupported providers ignore it.
        """
        ...
