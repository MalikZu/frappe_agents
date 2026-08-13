# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The adapter between the agent loop and `call_model`.

Two translations have to be exact, because everything the model sees and
everything the loop does comes through them: the transcript the loop holds must
arrive at `call_model` in the shape it has always been sent, and the reply must
come back as the content blocks the loop reads.

No model is ever called: `call_model` is patched, and these tests assert on what
it was handed and on what the adapter made of what it returned.
"""

import asyncio
import threading
from unittest.mock import patch

from frappe_agents.harness.messages import (
	AssistantMessage,
	TextContent,
	ToolCall,
	ToolResultMessage,
	UserMessage,
)
from frappe_agents.harness.provider_events import AssistantDoneEvent, AssistantStartEvent
from frappe_agents.harness.tools import AgentTool, AgentToolResult
from frappe_agents.runner.providers import ProviderError
from frappe_agents.runner.stream_adapter import ModelProfileProvider
from frappe_agents.tests.fixtures import PROFILE, AgentTestCase

SYSTEM = "You are an assistant working inside a Frappe site."

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


def text_reply(text: str = "Three tickets are open.", tokens_in: int = 120, tokens_out: int = 40) -> dict:
	return {"text": text, "tool_calls": [], "tokens_in": tokens_in, "tokens_out": tokens_out}


def call_reply(args, text=None, tokens_in: int = 200, tokens_out: int = 60) -> dict:
	return {
		"text": text,
		"tool_calls": [{"id": "call_1", "name": "search_documents", "args": args}],
		"tokens_in": tokens_in,
		"tokens_out": tokens_out,
	}


class TestStreamAdapter(AgentTestCase):
	def stream(self, provider, messages=None, tools=None, system: str = SYSTEM) -> list:
		"""Drive one `stream_response` to exhaustion and return its events."""

		async def collect():
			return [
				event
				async for event in provider.stream_response(
					model=PROFILE,
					system=system,
					messages=messages if messages is not None else [UserMessage(content="How many?")],
					tools=tools if tools is not None else [SEARCH_TOOL],
				)
			]

		return asyncio.run(collect())

	def ask(self, reply, **kwargs) -> tuple[list, object]:
		"""One exchange against a canned reply. Returns the events and the mock."""
		provider = ModelProfileProvider(PROFILE)
		with patch("frappe_agents.runner.stream_adapter.call_model") as call_model:
			if isinstance(reply, list):
				call_model.side_effect = reply
				events = [self.stream(provider, **kwargs) for _ in reply]
			else:
				call_model.return_value = reply
				events = self.stream(provider, **kwargs)
		self.provider = provider
		return events, call_model

	# --- what comes back -----------------------------------------------------

	def test_a_text_reply_becomes_a_start_and_a_done(self):
		events, _ = self.ask(text_reply())

		self.assertEqual(len(events), 2)
		self.assertIsInstance(events[0], AssistantStartEvent)
		self.assertIsInstance(events[1], AssistantDoneEvent)
		self.assertEqual(events[0].partial.content, [])
		self.assertEqual(events[1].reason, "stop")
		self.assertEqual(events[1].message.text, "Three tickets are open.")
		self.assertEqual(events[1].message.tool_calls, ())
		self.assertEqual(events[1].message.stop_reason, "stop")
		self.assertEqual(events[1].message.model, PROFILE)

	def test_a_tool_call_reply_is_a_tool_call_block(self):
		events, _ = self.ask(call_reply({"doctype": "FA Test Ticket"}))
		message = events[1].message

		self.assertEqual(events[1].reason, "toolUse")
		self.assertEqual(message.stop_reason, "toolUse")
		self.assertEqual(len(message.tool_calls), 1)
		call = message.tool_calls[0]
		self.assertEqual(call.id, "call_1")
		self.assertEqual(call.name, "search_documents")
		self.assertEqual(call.arguments, {"doctype": "FA Test Ticket"})
		# No text alongside the call, so no empty text block either.
		self.assertEqual(message.content, [call])

	def test_text_and_a_tool_call_arrive_in_that_order(self):
		events, _ = self.ask(call_reply({"doctype": "FA Test Ticket"}, text="Let me look."))
		content = events[1].message.content

		self.assertIsInstance(content[0], TextContent)
		self.assertIsInstance(content[1], ToolCall)

	def test_arguments_sent_as_a_json_string_are_parsed(self):
		"""One wire format sends an object, the other a string holding one."""
		events, _ = self.ask(call_reply('{"doctype": "FA Test Ticket", "limit": 10}'))

		self.assertEqual(
			events[1].message.tool_calls[0].arguments,
			{"doctype": "FA Test Ticket", "limit": 10},
		)

	def test_unusable_arguments_become_an_empty_object(self):
		"""A malformed argument list is the model's mistake, not the run's to die on."""
		for args in ("not json at all", "[1, 2, 3]", None):
			with self.subTest(args=args):
				events, _ = self.ask(call_reply(args))
				self.assertEqual(events[1].message.tool_calls[0].arguments, {})

	def test_a_call_with_no_id_still_gets_one(self):
		reply = {"text": None, "tool_calls": [{"name": "search_documents", "args": {}}]}
		events, _ = self.ask(reply)

		self.assertEqual(events[1].message.tool_calls[0].id, "call_0")

	# --- tokens --------------------------------------------------------------

	def test_tokens_accumulate_over_the_run(self):
		"""The runner reads these off the adapter once the loop has ended."""
		events, _ = self.ask(
			[text_reply(tokens_in=120, tokens_out=40), text_reply(tokens_in=300, tokens_out=90)]
		)

		self.assertEqual(self.provider.tokens_in, 420)
		self.assertEqual(self.provider.tokens_out, 130)
		# Each answer also carries its own cost.
		self.assertEqual(events[1][1].message.usage.input, 300)
		self.assertEqual(events[1][1].message.usage.output, 90)
		self.assertEqual(events[1][1].message.usage.total_tokens, 390)

	def test_a_reply_that_reports_no_usage_counts_as_nothing(self):
		self.ask({"text": "Done.", "tool_calls": []})

		self.assertEqual(self.provider.tokens_in, 0)
		self.assertEqual(self.provider.tokens_out, 0)

	# --- what goes out -------------------------------------------------------

	def test_the_system_prompt_leads_the_message_list(self):
		_, call_model = self.ask(text_reply())
		messages = call_model.call_args.args[1]

		self.assertEqual(messages[0], {"role": "system", "content": SYSTEM})
		self.assertEqual(messages[1], {"role": "user", "content": "How many?"})

	def test_the_profile_the_adapter_holds_is_the_one_called(self):
		_, call_model = self.ask(text_reply())

		self.assertEqual(call_model.call_args.args[0], PROFILE)

	def test_a_transcript_arrives_in_the_shape_call_model_accepts(self):
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
		_, call_model = self.ask(text_reply(), messages=messages)

		self.assertEqual(
			call_model.call_args.args[1][1:],
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
		_, call_model = self.ask(text_reply(), messages=messages)

		self.assertEqual(call_model.call_args.args[1][1], {"role": "assistant", "content": "Three are open."})

	def test_tools_are_sent_as_the_schemas_the_request_builders_read(self):
		_, call_model = self.ask(text_reply())

		self.assertEqual(
			call_model.call_args.args[2],
			[
				{
					"name": "search_documents",
					"description": "Find documents of one doctype.",
					"args_schema": SEARCH_SCHEMA,
				}
			],
		)

	def test_an_agent_with_no_tools_sends_no_schemas(self):
		_, call_model = self.ask(text_reply(), tools=[])

		self.assertEqual(call_model.call_args.args[2], [])

	# --- how it is called ----------------------------------------------------

	def test_the_blocking_call_runs_off_the_event_loop(self):
		"""It blocks for up to two minutes. On the loop thread nothing else could run."""
		threads = []

		def remember(*args, **kwargs):
			threads.append(threading.current_thread())
			return text_reply()

		provider = ModelProfileProvider(PROFILE)
		with patch("frappe_agents.runner.stream_adapter.call_model", side_effect=remember):
			self.stream(provider)

		self.assertEqual(len(threads), 1)
		self.assertIsNot(threads[0], threading.main_thread())

	def test_a_provider_error_is_raised_to_the_caller(self):
		"""The run records the provider's own words. Swallowing it would lose them."""
		provider = ModelProfileProvider(PROFILE)
		with patch("frappe_agents.runner.stream_adapter.call_model") as call_model:
			call_model.side_effect = ProviderError("Model request to http://localhost:1 failed.")
			with self.assertRaises(ProviderError):
				self.stream(provider)
