# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The bridge between the agent loop and `call_model`.

The loop speaks the harness protocol: message models in, assistant events out.
`call_model` speaks the shape this app has always sent: a list of role/content
dicts in, one reply dict out. This module is the only place the two meet.

Nothing here talks to a provider. `providers.py` still owns the HTTP call, the
API key and the two wire formats, and it is unchanged.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from frappe.utils import cint

from frappe_agents.harness.messages import (
	AgentMessage,
	AssistantMessage,
	ToolCall,
	ToolResultMessage,
	Usage,
	assistant_content,
	message_text,
)
from frappe_agents.harness.provider import CancellationToken
from frappe_agents.harness.provider_events import (
	AssistantDoneEvent,
	AssistantMessageEvent,
	AssistantStartEvent,
)
from frappe_agents.harness.tools import AgentTool
from frappe_agents.runner.providers import call_model


class ModelProfileProvider:
	"""The loop's model provider, backed by an LLM Model Profile.

	One instance per run. It counts the run's tokens itself, because the harness
	message models forbid extra fields and there is nowhere else to keep a total
	the runner can read once the loop has ended.

	The answer is not streamed. `call_model` returns a whole reply, so the loop
	gets the smallest honest pair of events: a start carrying an empty message,
	then a done carrying the finished one. Real streaming would be written here,
	and the loop would not change.
	"""

	def __init__(self, profile: Any) -> None:
		self.profile = profile
		self.tokens_in = 0
		self.tokens_out = 0

	async def stream_response(
		self,
		*,
		model: str,
		system: str,
		messages: list[AgentMessage],
		tools: list[AgentTool],
		signal: CancellationToken | None = None,
		session_id: str | None = None,
	) -> AsyncIterator[AssistantMessageEvent]:
		"""Ask the model once and yield the reply as two events.

		Cancellation is not checked here. The loop checks it at the top of every
		turn and again around every tool call, and a request already on the wire
		cannot be taken back anyway.
		"""
		yield AssistantStartEvent(partial=AssistantMessage(model=model))

		# call_model blocks for up to two minutes. Run on the event loop thread it
		# would hold up everything else the run is doing.
		reply = await asyncio.to_thread(
			call_model, self.profile, wire_messages(system, messages), tool_schemas(tools)
		)

		tokens_in = cint(reply.get("tokens_in"))
		tokens_out = cint(reply.get("tokens_out"))
		self.tokens_in += tokens_in
		self.tokens_out += tokens_out

		calls = tool_calls(reply)
		message = AssistantMessage(
			model=model,
			content=assistant_content(reply.get("text") or "", calls),
			usage=Usage(
				input=tokens_in,
				output=tokens_out,
				total_tokens=tokens_in + tokens_out,
			),
			stop_reason="toolUse" if calls else "stop",
		)
		yield AssistantDoneEvent(reason="toolUse" if calls else "stop", message=message)


def wire_messages(system: str, messages: list[AgentMessage]) -> list[dict]:
	"""Harness transcript out, the message list `call_model` accepts in.

	The system prompt is a message here, as it always has been. The Anthropic
	builder in `providers.py` lifts it back out into its own field.
	"""
	wire = [{"role": "system", "content": system}]
	wire.extend(_wire_message(message) for message in messages)
	return wire


def tool_schemas(tools: list[AgentTool]) -> list[dict]:
	"""The tool shape the loop holds, back in the shape the request builders read."""
	return [
		{
			"name": tool.name,
			"description": tool.description,
			"args_schema": dict(tool.parameters),
		}
		for tool in tools
	]


def tool_calls(reply: dict) -> list[ToolCall]:
	"""The reply's tool calls as content blocks."""
	calls = []
	for index, call in enumerate(reply.get("tool_calls") or []):
		calls.append(
			ToolCall(
				id=call.get("id") or f"call_{index}",
				name=call.get("name") or "",
				arguments=_arguments(call.get("args")),
			)
		)
	return calls


def _wire_message(message: AgentMessage) -> dict:
	if isinstance(message, AssistantMessage):
		wire: dict[str, Any] = {"role": "assistant", "content": message.text}
		if message.tool_calls:
			wire["tool_calls"] = [
				{"id": call.id, "name": call.name, "args": dict(call.arguments)}
				for call in message.tool_calls
			]
		return wire

	if isinstance(message, ToolResultMessage):
		return {
			"role": "tool",
			"tool_call_id": message.tool_call_id,
			"name": message.tool_name,
			"content": message.text,
		}

	# A user turn, and anything else the transcript carries — a summary, a note
	# written by the app itself. The model reads all of it as the user's side.
	return {"role": "user", "content": message_text(message)}


def _arguments(raw: Any) -> dict:
	"""Tool arguments as an object, whichever way the provider sent them.

	The two wire formats disagree: Anthropic sends a JSON object, OpenAI sends a
	string holding JSON. `call_model` already parses the string, so this is the
	belt to that braces — and it fails to an empty object rather than raising,
	because a malformed argument list is the model's mistake to be told about,
	not the run's to die on.
	"""
	if isinstance(raw, dict):
		return raw
	if not raw:
		return {}
	try:
		args = json.loads(raw)
	except Exception:
		return {}
	return args if isinstance(args, dict) else {}
