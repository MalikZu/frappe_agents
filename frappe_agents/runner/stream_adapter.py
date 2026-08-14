# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The bridge between the agent loop and the model stream.

The loop speaks the harness protocol: message models in, assistant events out.
`call_model_stream` speaks the shape this app has always sent — a list of
role/content dicts in — and answers with the normalized chunks documented at the
top of `providers.py`. This module is the only place the two meet.

Nothing here talks to a provider. `providers.py` still owns the HTTP call, the
API key and the two wire formats.

Two translations happen here, and both have to be exact:

* the transcript out — the loop's messages become the wire list. An assistant
  turn that asked for tools carries its thinking blocks and their signatures
  with it, because Anthropic verifies them byte for byte on the next call. The
  OpenAI-compatible builder ignores them, which is what that format wants.
* the stream in — each chunk becomes the harness event for it, and the assistant
  message is assembled block by block as it arrives. The loop reads the finished
  message off the last event.

The stream is pulled on a worker thread, one chunk at a time, because reading a
socket blocks and the event loop is running the rest of the turn. Between chunks
the cancellation token is read: on cancel the connection is closed and the turn
ends as aborted. A request already on the wire cannot be taken back, so this is
the earliest honest place to stop.

Between chunks is not the only place a run is stopped, though — a cancellation
can arrive while the worker thread sits inside a read that will not return until
the provider says something or the idle timeout does. So the turn also asks the
token to tell it when it trips, and closes the response from there: the blocked
read ends on a dead socket, the thread comes back, and the turn aborts.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

from frappe.utils import cint

from frappe_agents.harness.messages import (
	AgentMessage,
	AssistantContent,
	AssistantMessage,
	TextContent,
	ThinkingContent,
	ToolCall,
	ToolResultMessage,
	Usage,
	message_text,
)
from frappe_agents.harness.provider import CancellationToken
from frappe_agents.harness.provider_events import (
	AssistantDoneEvent,
	AssistantErrorEvent,
	AssistantMessageEvent,
	AssistantStartEvent,
	TextDeltaEvent,
	TextEndEvent,
	TextStartEvent,
	ThinkingDeltaEvent,
	ThinkingEndEvent,
	ThinkingStartEvent,
	ToolCallDeltaEvent,
	ToolCallEndEvent,
	ToolCallStartEvent,
)
from frappe_agents.harness.tools import AgentTool
from frappe_agents.runner.providers import call_model_stream

# The loop's own words for a cancelled turn. Said the same way here so a run that
# was stopped mid-answer reports what a run stopped between turns reports.
ABORTED = "Operation aborted"

DONE_REASONS = ("stop", "length", "toolUse")

# Told apart from a chunk, which is always a dict.
_EXHAUSTED = object()


class ModelProfileProvider:
	"""The loop's model provider, backed by an LLM Model Profile.

	One instance per run. It counts the run's tokens itself, because the harness
	message models forbid extra fields and there is nowhere else to keep a total
	the runner can read once the loop has ended.
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
		"""Ask the model once and yield the answer as it is written."""
		yield AssistantStartEvent(partial=AssistantMessage(model=model))

		# Resolved here, on this thread: a disabled provider or a missing key is
		# a frappe read, and it fails now rather than on a worker thread.
		chunks = call_model_stream(self.profile, wire_messages(system, messages), tool_schemas(tools))

		answer = _Assembly(model)
		counted_in = 0
		counted_out = 0
		finished = False
		released = False

		def release() -> None:
			"""Cancelled: take the socket away so a blocked read ends now."""
			nonlocal released
			released = True
			_close_response(chunks)

		# A cancellation that arrives while this turn is waiting on the socket has
		# nothing to interrupt — the check below only runs between chunks. So the
		# token is asked to say when it trips, and what it trips is the connection.
		watch = getattr(signal, "on_cancel", None)
		if watch is not None:
			watch(release)

		try:
			while True:
				if signal is not None and signal.is_cancelled():
					yield AssistantErrorEvent(reason="aborted", error=answer.aborted())
					return

				# One chunk at a time, off the event loop: reading the socket
				# blocks, and the turn has other work waiting on this thread.
				try:
					chunk = await asyncio.to_thread(next, chunks, _EXHAUSTED)
				except Exception:
					# A read that ended because the cancellation closed the socket
					# under it. The turn was stopped, not broken.
					if not released:
						raise
					chunk = _EXHAUSTED

				if released:
					# Whatever this was, it arrived after the run was stopped, and
					# a stopped run keeps none of it.
					yield AssistantErrorEvent(reason="aborted", error=answer.aborted())
					return

				if chunk is _EXHAUSTED:
					break

				kind = chunk.get("type")
				if kind == "usage":
					# The chunk carries this call's totals so far, not an
					# increment, so the run's totals move by the difference.
					answer.count(chunk)
					self.tokens_in += answer.tokens_in - counted_in
					self.tokens_out += answer.tokens_out - counted_out
					counted_in, counted_out = answer.tokens_in, answer.tokens_out
					continue

				if kind == "message_end":
					finished = True
					yield answer.done(chunk.get("reason"))
					break

				for event in answer.translate(chunk):
					yield event
		finally:
			forget = getattr(signal, "forget_cancel", None)
			if forget is not None:
				forget(release)
			_close(chunks)

		if not finished:
			# The stream stopped without the parser's last word. What arrived is
			# still an answer, and the loop has to be given one.
			yield answer.done(None)


class _Assembly:
	"""The assistant message being written, block by block.

	One content block per content index, held in the order the stream opened
	them. Every event carries a copy of the message as it stands, so an event
	kept by a test or a buffer is not rewritten by the delta after it.
	"""

	def __init__(self, model: str) -> None:
		self.model = model
		self.blocks: dict[int, AssistantContent] = {}
		self.order: list[int] = []
		self.tokens_in = 0
		self.tokens_out = 0

	def translate(self, chunk: dict) -> Iterator[AssistantMessageEvent]:
		"""One normalized chunk as the harness event for it."""
		kind = chunk.get("type")
		index = cint(chunk.get("index"))
		text = chunk.get("text") or ""

		if kind == "text_start":
			self._put(index, TextContent(text=""))
			yield TextStartEvent(content_index=index, partial=self.partial())

		elif kind == "text_delta":
			block = self._text(index)
			block.text += text
			yield TextDeltaEvent(content_index=index, delta=text, partial=self.partial())

		elif kind == "text_end":
			# The stream's own word for the whole block, and it is the canonical
			# one: a block can open holding text that no delta ever repeated.
			self._text(index).text = text
			yield TextEndEvent(content_index=index, content=text, partial=self.partial())

		elif kind == "thinking_start":
			self._put(index, ThinkingContent(thinking="", redacted=bool(chunk.get("redacted"))))
			yield ThinkingStartEvent(content_index=index, partial=self.partial())

		elif kind == "thinking_delta":
			block = self._thinking(index)
			block.thinking += text
			yield ThinkingDeltaEvent(content_index=index, delta=text, partial=self.partial())

		elif kind == "thinking_end":
			block = self._thinking(index)
			block.thinking = text
			block.thinking_signature = chunk.get("signature") or None
			block.redacted = bool(chunk.get("redacted"))
			yield ThinkingEndEvent(content_index=index, content=text, partial=self.partial())

		elif kind == "toolcall_start":
			self._put(index, _call(chunk, index))
			yield ToolCallStartEvent(content_index=index, partial=self.partial())

		elif kind == "toolcall_delta":
			# A raw fragment of the argument JSON. It cannot go into `arguments`
			# until the call closes and the whole of it parses.
			yield ToolCallDeltaEvent(content_index=index, delta=text, partial=self.partial())

		elif kind == "toolcall_end":
			call = _call(chunk, index)
			self._put(index, call)
			yield ToolCallEndEvent(content_index=index, tool_call=call.model_copy(), partial=self.partial())

	def count(self, chunk: dict) -> None:
		"""This call's token totals, as the stream last reported them."""
		self.tokens_in = cint(chunk.get("tokens_in"))
		self.tokens_out = cint(chunk.get("tokens_out"))

	def partial(self) -> AssistantMessage:
		"""The message as it stands, blocks and all — including empty ones."""
		return AssistantMessage(
			model=self.model,
			content=[self.blocks[index].model_copy() for index in self.order],
		)

	def done(self, reason: str | None) -> AssistantDoneEvent:
		"""The finished message, as the event the loop reads it off."""
		message = self.message(reason)
		return AssistantDoneEvent(reason=message.stop_reason, message=message)

	def message(self, reason: str | None) -> AssistantMessage:
		content = [
			self.blocks[index].model_copy() for index in self.order if _has_content(self.blocks[index])
		]
		if reason not in DONE_REASONS:
			# A stream that died before it said why. What it asked for decides.
			reason = "toolUse" if any(isinstance(block, ToolCall) for block in content) else "stop"
		return AssistantMessage(
			model=self.model,
			content=content,
			usage=self.usage(),
			stop_reason=reason,
		)

	def aborted(self) -> AssistantMessage:
		"""The turn as it ends when the run is cancelled mid-answer.

		Deliberately empty. The loop keeps a failed turn in its history for the
		record but never sends it to the model again, and half an answer beside
		an unfinished tool call is not context anything could use. What the call
		cost is kept, because it was billed.
		"""
		return AssistantMessage(
			model=self.model,
			content=[],
			usage=self.usage(),
			stop_reason="aborted",
			error_message=ABORTED,
		)

	def usage(self) -> Usage:
		return Usage(
			input=self.tokens_in,
			output=self.tokens_out,
			total_tokens=self.tokens_in + self.tokens_out,
		)

	def _put(self, index: int, block: AssistantContent) -> None:
		if index not in self.blocks:
			self.order.append(index)
		self.blocks[index] = block

	def _text(self, index: int) -> TextContent:
		block = self.blocks.get(index)
		if not isinstance(block, TextContent):
			block = TextContent(text="")
			self._put(index, block)
		return block

	def _thinking(self, index: int) -> ThinkingContent:
		block = self.blocks.get(index)
		if not isinstance(block, ThinkingContent):
			block = ThinkingContent(thinking="")
			self._put(index, block)
		return block


def wire_messages(system: str, messages: list[AgentMessage]) -> list[dict]:
	"""Harness transcript out, the message list the providers accept in.

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


def _call(chunk: dict, index: int) -> ToolCall:
	return ToolCall(
		id=chunk.get("id") or f"call_{index}",
		name=chunk.get("name") or "",
		arguments=_arguments(chunk.get("args") or chunk.get("arguments")),
	)


def _has_content(block: AssistantContent) -> bool:
	"""Whether a block says anything. An empty one is not worth keeping."""
	if isinstance(block, TextContent):
		return bool(block.text)
	if isinstance(block, ThinkingContent):
		return bool(block.thinking)
	return True


def _close_response(chunks: Any) -> None:
	"""Drop the socket under a read that is blocked on it, if the stream has one.

	Only `ProviderStream` does. A test's scripted stream and a plain generator do
	not, and for them cancellation stays what it was: noticed between chunks.
	"""
	close = getattr(chunks, "close_response", None)
	if close is None:
		return
	try:
		close()
	except Exception:
		pass


def _close(chunks: Any) -> None:
	"""Let go of the stream, whatever state it is in.

	Closing the generator unwinds it through the `with` that holds the response
	open, which is how the socket goes away. It is called on every path out,
	including the one where the stream already ended.
	"""
	close = getattr(chunks, "close", None)
	if close is None:
		return
	try:
		close()
	except Exception:
		pass


def _wire_message(message: AgentMessage) -> dict:
	if isinstance(message, AssistantMessage):
		wire: dict[str, Any] = {"role": "assistant", "content": message.text}
		if message.tool_calls:
			wire["tool_calls"] = [
				{"id": call.id, "name": call.name, "args": dict(call.arguments)}
				for call in message.tool_calls
			]
			thinking = _wire_thinking(message)
			if thinking:
				wire["thinking"] = thinking
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


def _wire_thinking(message: AssistantMessage) -> list[dict]:
	"""The thinking blocks that have to travel back with a tool call.

	Anthropic will not accept a turn that asked for a tool unless the reasoning
	that led to it comes back exactly as it was sent, signature and all. A block
	with no signature is dropped rather than sent: it would be rejected, and the
	whole turn with it. Nothing here reaches an OpenAI-compatible endpoint —
	that builder reads role, content and tool calls, and nothing else.
	"""
	blocks = []
	for block in message.content:
		if not isinstance(block, ThinkingContent) or not block.thinking:
			continue
		if block.redacted:
			blocks.append({"thinking": block.thinking, "redacted": True})
		elif block.thinking_signature:
			blocks.append({"thinking": block.thinking, "signature": block.thinking_signature})
	return blocks


def _arguments(raw: Any) -> dict:
	"""Tool arguments as an object, whichever way the provider sent them.

	The two wire formats disagree: Anthropic sends a JSON object, OpenAI sends a
	string holding JSON. The stream parser already parses the string, so this is
	the belt to that braces — and it fails to an empty object rather than
	raising, because a malformed argument list is the model's mistake to be told
	about, not the run's to die on.
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
