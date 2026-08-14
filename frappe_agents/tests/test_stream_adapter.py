# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The adapter between the agent loop and the model stream.

Two translations have to be exact, because everything the model sees and
everything the loop does comes through them: the transcript the loop holds must
arrive at the provider in the shape it has always been sent, and the chunks that
come back must become the events the loop reads and the message it keeps.

No model is ever called and no socket is ever opened: `call_model_stream` is
patched with a scripted chunk stream, and these tests assert on what it was
handed, on what the adapter made of what it yielded, and on when it was closed.
"""

import asyncio
import threading
from unittest.mock import patch

from frappe_agents.harness.events import AgentEndEvent
from frappe_agents.harness.loop import run_agent_loop
from frappe_agents.harness.messages import (
	AssistantMessage,
	TextContent,
	ThinkingContent,
	ToolCall,
	ToolResultMessage,
	UserMessage,
)
from frappe_agents.harness.provider_events import (
	AssistantDoneEvent,
	AssistantErrorEvent,
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
from frappe_agents.harness.tools import AgentTool, AgentToolResult
from frappe_agents.runner.providers import (
	ProviderError,
	_anthropic_messages,
	_openai_messages,
)
from frappe_agents.runner.stream_adapter import ModelProfileProvider
from frappe_agents.tests.fixtures import PROFILE, AgentTestCase

SYSTEM = "You are an assistant working inside a Frappe site."
SIGNATURE = "ErUBCkYIBBgCIkDf9k2Lm+verbatim+or+the+turn+is+rejected"

SEARCH_SCHEMA = {
	"type": "object",
	"properties": {"doctype": {"type": "string"}},
	"required": ["doctype"],
}


async def _noop(tool_call_id, arguments, signal=None, on_update=None) -> AgentToolResult:
	return AgentToolResult(content="")


SEARCH_TOOL = AgentTool(
	name="search_documents",
	label="Search Documents",
	description="Find documents of one doctype.",
	parameters=SEARCH_SCHEMA,
	execute_fn=_noop,
)


# --- the chunks a provider yields ---------------------------------------------


def opened() -> dict:
	return {"type": "message_start", "model": PROFILE}


def usage(tokens_in: int, tokens_out: int) -> dict:
	return {"type": "usage", "tokens_in": tokens_in, "tokens_out": tokens_out}


def said(text: str, index: int = 0, pieces: int = 2) -> list[dict]:
	"""One text block, opened, written in pieces and closed."""
	size = max(1, -(-len(text) // pieces))
	return [
		{"type": "text_start", "index": index},
		*[
			{"type": "text_delta", "index": index, "text": text[start : start + size]}
			for start in range(0, len(text), size)
		],
		{"type": "text_end", "index": index, "text": text},
	]


def thought(text: str, index: int = 0, signature: str | None = SIGNATURE) -> list[dict]:
	return [
		{"type": "thinking_start", "index": index, "redacted": False},
		{"type": "thinking_delta", "index": index, "text": text},
		{
			"type": "thinking_end",
			"index": index,
			"text": text,
			"signature": signature,
			"redacted": False,
		},
	]


def called(args: str = '{"doctype": "FA Test Ticket"}', index: int = 0, call_id="call_1") -> list[dict]:
	return [
		{"type": "toolcall_start", "index": index, "id": call_id, "name": "search_documents"},
		{"type": "toolcall_delta", "index": index, "text": args},
		{
			"type": "toolcall_end",
			"index": index,
			"id": call_id,
			"name": "search_documents",
			"args": args,
			"arguments": args,
		},
	]


def ended(reason: str = "stop") -> dict:
	return {"type": "message_end", "reason": reason}


def text_stream(text: str = "Three tickets are open.", tokens_in: int = 120, tokens_out: int = 40):
	return [opened(), usage(tokens_in, tokens_out), *said(text), ended()]


class Stream:
	"""A scripted chunk stream that remembers how it was used.

	An entry that is an exception is raised instead of yielded, which is how a
	connection that breaks halfway through an answer is written down.
	"""

	def __init__(self, chunks) -> None:
		self.chunks = list(chunks)
		self.pulled = 0
		self.closed = False

	def __iter__(self):
		return self

	def __next__(self) -> dict:
		if self.closed or self.pulled >= len(self.chunks):
			raise StopIteration
		chunk = self.chunks[self.pulled]
		self.pulled += 1
		if isinstance(chunk, BaseException):
			raise chunk
		return chunk

	def close(self) -> None:
		self.closed = True


class Cancelled:
	"""A cancellation token that trips after a set number of reads."""

	def __init__(self, after: int = 0) -> None:
		self.after = after
		self.reads = 0

	def is_cancelled(self) -> bool:
		self.reads += 1
		return self.reads > self.after


class TestStreamAdapter(AgentTestCase):
	def stream(self, provider, messages=None, tools=None, system: str = SYSTEM, signal=None) -> list:
		"""Drive one `stream_response` to exhaustion and return its events."""

		async def collect():
			return [
				event
				async for event in provider.stream_response(
					model=PROFILE,
					system=system,
					messages=messages if messages is not None else [UserMessage(content="How many?")],
					tools=tools if tools is not None else [SEARCH_TOOL],
					signal=signal,
				)
			]

		return asyncio.run(collect())

	def ask(self, chunks, **kwargs) -> tuple[list, object]:
		"""One exchange against a scripted stream. Returns the events and the mock.

		A list of lists is a script of several answers, one per model call.
		"""
		provider = ModelProfileProvider(PROFILE)
		with patch("frappe_agents.runner.stream_adapter.call_model_stream") as call:
			if chunks and isinstance(chunks[0], list):
				self.streams = [Stream(answer) for answer in chunks]
				call.side_effect = list(self.streams)
				events = [self.stream(provider, **kwargs) for _ in chunks]
			else:
				self.streams = [Stream(chunks)]
				call.return_value = self.streams[0]
				events = self.stream(provider, **kwargs)
		self.provider = provider
		return events, call

	def types(self, events) -> list[str]:
		return [event.type for event in events]

	# --- what comes back -----------------------------------------------------

	def test_a_text_answer_arrives_as_the_events_that_wrote_it(self):
		events, _ = self.ask(text_stream("Three tickets are open."))

		self.assertEqual(
			self.types(events),
			["start", "text_start", "text_delta", "text_delta", "text_end", "done"],
		)
		self.assertIsInstance(events[0], AssistantStartEvent)
		self.assertEqual(events[0].partial.content, [])
		self.assertIsInstance(events[1], TextStartEvent)
		self.assertIsInstance(events[2], TextDeltaEvent)
		self.assertEqual("".join(event.delta for event in events[2:4]), "Three tickets are open.")
		self.assertIsInstance(events[4], TextEndEvent)
		self.assertEqual(events[4].content, "Three tickets are open.")

		done = events[-1]
		self.assertIsInstance(done, AssistantDoneEvent)
		self.assertEqual(done.reason, "stop")
		self.assertEqual(done.message.text, "Three tickets are open.")
		self.assertEqual(done.message.stop_reason, "stop")
		self.assertEqual(done.message.model, PROFILE)
		self.assertEqual(done.message.tool_calls, ())

	def test_the_partial_grows_one_delta_at_a_time(self):
		"""Every event carries the message as it stood when it was sent."""
		events, _ = self.ask(text_stream("Three tickets are open."))

		self.assertEqual(events[1].partial.text, "")
		self.assertEqual(events[2].partial.text, "Three ticket")
		self.assertEqual(events[3].partial.text, "Three tickets are open.")

	def test_thinking_text_and_a_tool_call_come_back_as_three_blocks(self):
		chunks = [
			opened(),
			*thought("The user wants a count.", index=0),
			*said("Let me look.", index=1, pieces=1),
			*called(index=2),
			usage(200, 60),
			ended("toolUse"),
		]

		events, _ = self.ask(chunks)

		self.assertEqual(
			self.types(events),
			[
				"start",
				"thinking_start",
				"thinking_delta",
				"thinking_end",
				"text_start",
				"text_delta",
				"text_end",
				"toolcall_start",
				"toolcall_delta",
				"toolcall_end",
				"done",
			],
		)
		self.assertIsInstance(events[1], ThinkingStartEvent)
		self.assertIsInstance(events[2], ThinkingDeltaEvent)
		self.assertIsInstance(events[3], ThinkingEndEvent)
		self.assertIsInstance(events[7], ToolCallStartEvent)
		self.assertIsInstance(events[8], ToolCallDeltaEvent)
		self.assertIsInstance(events[9], ToolCallEndEvent)
		self.assertEqual(events[9].tool_call.name, "search_documents")

		content = events[-1].message.content
		self.assertIsInstance(content[0], ThinkingContent)
		self.assertEqual(content[0].thinking, "The user wants a count.")
		self.assertEqual(content[0].thinking_signature, SIGNATURE)
		self.assertIsInstance(content[1], TextContent)
		self.assertEqual(content[1].text, "Let me look.")
		self.assertIsInstance(content[2], ToolCall)
		self.assertEqual(content[2].arguments, {"doctype": "FA Test Ticket"})
		self.assertEqual(events[-1].reason, "toolUse")
		self.assertEqual(events[-1].message.stop_reason, "toolUse")

	def test_a_block_that_said_nothing_is_not_kept(self):
		"""A text block can open and close with nothing written in it."""
		chunks = [
			opened(),
			{"type": "text_start", "index": 0},
			{"type": "text_end", "index": 0, "text": ""},
			*called(index=1),
			ended("toolUse"),
		]

		events, _ = self.ask(chunks)

		self.assertEqual(len(events[-1].message.content), 1)
		self.assertIsInstance(events[-1].message.content[0], ToolCall)

	def test_a_redacted_thinking_block_is_kept_and_marked(self):
		chunks = [
			opened(),
			{"type": "thinking_start", "index": 0, "redacted": True},
			{
				"type": "thinking_end",
				"index": 0,
				"text": "EvgBCkYIBBgC",
				"signature": None,
				"redacted": True,
			},
			*said("Done.", index=1, pieces=1),
			ended(),
		]

		events, _ = self.ask(chunks)
		block = events[-1].message.content[0]

		self.assertIsInstance(block, ThinkingContent)
		self.assertTrue(block.redacted)
		self.assertEqual(block.thinking, "EvgBCkYIBBgC")

	def test_arguments_sent_as_a_json_string_are_parsed(self):
		"""One wire format sends an object, the other a string holding one."""
		events, _ = self.ask(
			[opened(), *called('{"doctype": "FA Test Ticket", "limit": 10}'), ended("toolUse")]
		)

		self.assertEqual(
			events[-1].message.tool_calls[0].arguments,
			{"doctype": "FA Test Ticket", "limit": 10},
		)

	def test_unusable_arguments_become_an_empty_object(self):
		"""A malformed argument list is the model's mistake, not the run's to die on."""
		for args in ("not json at all", "[1, 2, 3]", ""):
			with self.subTest(args=args):
				events, _ = self.ask([opened(), *called(args), ended("toolUse")])
				self.assertEqual(events[-1].message.tool_calls[0].arguments, {})

	def test_a_call_with_no_id_still_gets_one(self):
		chunks = [
			opened(),
			{"type": "toolcall_start", "index": 0, "id": "", "name": "search_documents"},
			{
				"type": "toolcall_end",
				"index": 0,
				"id": "",
				"name": "search_documents",
				"args": {},
				"arguments": "",
			},
			ended("toolUse"),
		]

		events, _ = self.ask(chunks)

		self.assertEqual(events[-1].message.tool_calls[0].id, "call_0")

	def test_a_stream_that_stops_early_still_finishes_the_message(self):
		"""The connection died after the answer and before the parser's last word."""
		events, _ = self.ask([opened(), *said("Three are open.", pieces=1)])

		self.assertIsInstance(events[-1], AssistantDoneEvent)
		self.assertEqual(events[-1].reason, "stop")
		self.assertEqual(events[-1].message.text, "Three are open.")

	def test_an_answer_cut_off_by_the_token_ceiling_says_so(self):
		events, _ = self.ask([opened(), *said("Three are", pieces=1), ended("length")])

		self.assertEqual(events[-1].reason, "length")
		self.assertEqual(events[-1].message.stop_reason, "length")

	# --- tokens --------------------------------------------------------------

	def test_tokens_accumulate_over_the_run(self):
		"""The runner reads these off the adapter once the loop has ended."""
		events, _ = self.ask(
			[
				text_stream(tokens_in=120, tokens_out=40),
				text_stream(tokens_in=300, tokens_out=90),
			]
		)

		self.assertEqual(self.provider.tokens_in, 420)
		self.assertEqual(self.provider.tokens_out, 130)
		# Each answer also carries its own cost.
		self.assertEqual(events[1][-1].message.usage.input, 300)
		self.assertEqual(events[1][-1].message.usage.output, 90)
		self.assertEqual(events[1][-1].message.usage.total_tokens, 390)

	def test_a_usage_frame_replaces_the_one_before_it(self):
		"""Usage is the call's running total, not an increment. Adding them doubles."""
		chunks = [opened(), usage(120, 5), *said("Done.", pieces=1), usage(120, 40), ended()]

		events, _ = self.ask(chunks)

		self.assertEqual(self.provider.tokens_in, 120)
		self.assertEqual(self.provider.tokens_out, 40)
		self.assertEqual(events[-1].message.usage.total_tokens, 160)

	def test_a_stream_that_reports_no_usage_counts_as_nothing(self):
		self.ask([opened(), *said("Done.", pieces=1), ended()])

		self.assertEqual(self.provider.tokens_in, 0)
		self.assertEqual(self.provider.tokens_out, 0)

	# --- cancellation --------------------------------------------------------

	def test_a_cancel_between_chunks_ends_the_turn_and_closes_the_stream(self):
		provider = ModelProfileProvider(PROFILE)
		stream = Stream([opened(), *said("Three tickets are open.", pieces=4), ended()])

		with patch("frappe_agents.runner.stream_adapter.call_model_stream", return_value=stream):
			# Read once at the top of the first pull, then cancelled.
			events = self.stream(provider, signal=Cancelled(after=1))

		self.assertIsInstance(events[-1], AssistantErrorEvent)
		self.assertEqual(events[-1].reason, "aborted")
		self.assertEqual(events[-1].error.stop_reason, "aborted")
		self.assertEqual(events[-1].error.error_message, "Operation aborted")
		self.assertTrue(stream.closed)
		# Stopped pulling: the whole answer was there and was not read.
		self.assertLess(stream.pulled, len(stream.chunks))

	def test_a_cancelled_turn_still_records_what_the_call_cost(self):
		provider = ModelProfileProvider(PROFILE)
		stream = Stream([opened(), usage(120, 5), *said("Three", pieces=3), ended()])

		with patch("frappe_agents.runner.stream_adapter.call_model_stream", return_value=stream):
			events = self.stream(provider, signal=Cancelled(after=3))

		self.assertEqual(provider.tokens_in, 120)
		self.assertEqual(events[-1].error.usage.input, 120)

	def test_a_cancelled_turn_carries_no_content_into_the_transcript(self):
		"""Half an answer beside an unfinished tool call is not context to reuse."""
		provider = ModelProfileProvider(PROFILE)
		stream = Stream([opened(), *said("Three tickets", pieces=4), ended()])

		with patch("frappe_agents.runner.stream_adapter.call_model_stream", return_value=stream):
			events = self.stream(provider, signal=Cancelled(after=3))

		self.assertEqual(events[-1].error.content, [])

	def test_a_stream_that_ends_normally_is_closed_too(self):
		self.ask(text_stream())

		self.assertTrue(self.streams[0].closed)

	# --- what goes out -------------------------------------------------------

	def test_the_system_prompt_leads_the_message_list(self):
		_, call = self.ask(text_stream())
		messages = call.call_args.args[1]

		self.assertEqual(messages[0], {"role": "system", "content": SYSTEM})
		self.assertEqual(messages[1], {"role": "user", "content": "How many?"})

	def test_the_profile_the_adapter_holds_is_the_one_called(self):
		_, call = self.ask(text_stream())

		self.assertEqual(call.call_args.args[0], PROFILE)

	def test_a_transcript_arrives_in_the_shape_the_providers_accept(self):
		messages = [
			UserMessage(content="How many tickets are open?"),
			AssistantMessage(
				model=PROFILE,
				content=[
					TextContent(text="Let me look."),
					ToolCall(id="call_1", name="search_documents", arguments={"doctype": "FA Test Ticket"}),
				],
			),
			ToolResultMessage(
				tool_call_id="call_1",
				tool_name="search_documents",
				content=[TextContent(text='{"ok": true}')],
			),
			UserMessage(content="And closed?"),
		]
		_, call = self.ask(text_stream(), messages=messages)

		self.assertEqual(
			call.call_args.args[1][1:],
			[
				{"role": "user", "content": "How many tickets are open?"},
				{
					"role": "assistant",
					"content": "Let me look.",
					"tool_calls": [
						{"id": "call_1", "name": "search_documents", "args": {"doctype": "FA Test Ticket"}}
					],
				},
				{
					"role": "tool",
					"tool_call_id": "call_1",
					"name": "search_documents",
					"content": '{"ok": true}',
				},
				{"role": "user", "content": "And closed?"},
			],
		)

	def test_an_assistant_turn_without_tool_calls_carries_no_tool_calls_key(self):
		"""Prior conversation turns are replayed exactly as they were before."""
		messages = [AssistantMessage(model=PROFILE, content="Three are open.")]
		_, call = self.ask(text_stream(), messages=messages)

		self.assertEqual(call.call_args.args[1][1], {"role": "assistant", "content": "Three are open."})

	def test_tools_are_sent_as_the_schemas_the_request_builders_read(self):
		_, call = self.ask(text_stream())

		self.assertEqual(
			call.call_args.args[2],
			[
				{
					"name": "search_documents",
					"description": "Find documents of one doctype.",
					"args_schema": SEARCH_SCHEMA,
				}
			],
		)

	def test_an_agent_with_no_tools_sends_no_schemas(self):
		_, call = self.ask(text_stream(), tools=[])

		self.assertEqual(call.call_args.args[2], [])

	# --- thinking, on the way back out ---------------------------------------

	def thinking_turn(self, signature: str | None = SIGNATURE, calls: bool = True) -> AssistantMessage:
		content = [
			ThinkingContent(thinking="The user wants a count.", thinking_signature=signature),
			TextContent(text="Let me look."),
		]
		if calls:
			content.append(
				ToolCall(id="call_1", name="search_documents", arguments={"doctype": "FA Test Ticket"})
			)
		return AssistantMessage(model=PROFILE, content=content)

	def test_a_turn_that_asked_for_a_tool_carries_its_thinking_back(self):
		_, call = self.ask(text_stream(), messages=[self.thinking_turn()])

		self.assertEqual(
			call.call_args.args[1][1]["thinking"],
			[{"thinking": "The user wants a count.", "signature": SIGNATURE}],
		)

	def test_a_turn_that_only_answered_carries_no_thinking(self):
		"""Only a turn holding a tool call has to send its reasoning back."""
		_, call = self.ask(text_stream(), messages=[self.thinking_turn(calls=False)])

		self.assertNotIn("thinking", call.call_args.args[1][1])

	def test_thinking_without_a_signature_is_left_behind(self):
		"""Anthropic verifies the signature and rejects the whole turn without it."""
		_, call = self.ask(text_stream(), messages=[self.thinking_turn(signature=None)])

		self.assertNotIn("thinking", call.call_args.args[1][1])

	def test_the_anthropic_builder_sends_the_signature_verbatim_and_first(self):
		_, call = self.ask(text_stream(), messages=[self.thinking_turn()])

		_, converted = _anthropic_messages(call.call_args.args[1])
		blocks = converted[0]["content"]
		self.assertEqual(
			blocks[0],
			{
				"type": "thinking",
				"thinking": "The user wants a count.",
				"signature": SIGNATURE,
			},
		)
		self.assertEqual(blocks[1]["type"], "text")
		self.assertEqual(blocks[2]["type"], "tool_use")

	def test_a_redacted_block_goes_back_as_the_ciphertext_it_is(self):
		turn = AssistantMessage(
			model=PROFILE,
			content=[
				ThinkingContent(thinking="EvgBCkYIBBgC", redacted=True),
				ToolCall(id="call_1", name="search_documents", arguments={}),
			],
		)
		_, call = self.ask(text_stream(), messages=[turn])

		_, converted = _anthropic_messages(call.call_args.args[1])
		self.assertEqual(
			converted[0]["content"][0],
			{"type": "redacted_thinking", "data": "EvgBCkYIBBgC"},
		)

	def test_the_openai_builder_leaves_thinking_out(self):
		"""Standard, and safe: no OpenAI-compatible endpoint takes it back."""
		_, call = self.ask(text_stream(), messages=[self.thinking_turn()])

		converted = _openai_messages(call.call_args.args[1])
		self.assertNotIn("thinking", converted[1])
		self.assertEqual(converted[1]["content"], "Let me look.")

	def test_a_signature_survives_the_round_trip_to_the_next_turn(self):
		"""The rule that matters: what the stream said comes back byte for byte."""
		first = [
			opened(),
			*thought("The user wants a count."),
			*called(index=1),
			ended("toolUse"),
		]
		provider = ModelProfileProvider(PROFILE)

		async def drive():
			return [
				event
				async for event in run_agent_loop(
					provider=provider,
					model=PROFILE,
					system=SYSTEM,
					messages=[UserMessage(content="How many?")],
					tools=[SEARCH_TOOL],
					max_turns=5,
				)
			]

		with patch("frappe_agents.runner.stream_adapter.call_model_stream") as call:
			call.side_effect = [Stream(first), Stream(text_stream())]
			asyncio.run(drive())

		second = call.call_args_list[1].args[1]
		assistant = next(message for message in second if message["role"] == "assistant")
		self.assertEqual(
			assistant["thinking"],
			[{"thinking": "The user wants a count.", "signature": SIGNATURE}],
		)

		_, converted = _anthropic_messages(second)
		self.assertEqual(converted[1]["content"][0]["signature"], SIGNATURE)

	# --- how it is called ----------------------------------------------------

	def test_the_stream_is_pulled_off_the_event_loop(self):
		"""Reading the socket blocks. On the loop thread nothing else could run."""
		threads = []

		class Watched(Stream):
			def __next__(self):
				threads.append(threading.current_thread())
				return super().__next__()

		provider = ModelProfileProvider(PROFILE)
		stream = Watched(text_stream())
		with patch("frappe_agents.runner.stream_adapter.call_model_stream", return_value=stream):
			self.stream(provider)

		self.assertTrue(threads)
		self.assertNotIn(threading.main_thread(), threads)

	def test_a_provider_error_is_raised_to_the_caller(self):
		"""The run records the provider's own words. Swallowing it would lose them."""
		provider = ModelProfileProvider(PROFILE)
		error = ProviderError("Model request to http://localhost:1 failed.")
		with patch("frappe_agents.runner.stream_adapter.call_model_stream", side_effect=error):
			with self.assertRaises(ProviderError):
				self.stream(provider)

	def test_a_stream_that_breaks_halfway_raises_and_is_closed(self):
		provider = ModelProfileProvider(PROFILE)
		stream = Stream([opened(), ProviderError("Model stream from http://localhost:1 broke.")])

		with patch("frappe_agents.runner.stream_adapter.call_model_stream", return_value=stream):
			with self.assertRaises(ProviderError):
				self.stream(provider)

		self.assertTrue(stream.closed)

	# --- against the real loop -----------------------------------------------

	def test_the_loop_drives_the_adapter_through_a_tool_call_and_an_answer(self):
		"""The contract that matters: the loop accepts what the adapter yields."""
		provider = ModelProfileProvider(PROFILE)

		async def drive():
			return [
				event
				async for event in run_agent_loop(
					provider=provider,
					model=PROFILE,
					system=SYSTEM,
					messages=[UserMessage(content="How many?")],
					tools=[SEARCH_TOOL],
					max_turns=5,
				)
			]

		with patch("frappe_agents.runner.stream_adapter.call_model_stream") as call:
			call.side_effect = [
				Stream([opened(), usage(200, 60), *called(), ended("toolUse")]),
				Stream(text_stream(tokens_in=120, tokens_out=40)),
			]
			events = asyncio.run(drive())

		self.assertEqual(call.call_count, 2)
		# The tool result went back to the model as the next turn of the transcript.
		second = call.call_args_list[1].args[1]
		self.assertEqual(second[-1]["role"], "tool")
		self.assertEqual(second[-1]["tool_call_id"], "call_1")
		self.assertEqual(second[-2]["tool_calls"][0]["name"], "search_documents")

		self.assertIsInstance(events[-1], AgentEndEvent)
		self.assertEqual(events[-1].messages[-1].text, "Three tickets are open.")
		self.assertEqual(provider.tokens_in, 320)
		self.assertEqual(provider.tokens_out, 100)
