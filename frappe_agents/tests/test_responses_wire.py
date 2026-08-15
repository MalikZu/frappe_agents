# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The OpenAI Responses wire: what leaves this process, and what it does with
what comes back.

Responses is not chat completions with new field names. The system prompt is its
own top-level field rather than the first message; a turn is a list of items
rather than a list of messages; a tool call and its result are two separate
items paired on a `call_id`; and the reasoning that led to a tool call is an
item in its own right that has to be handed back on the next turn or the model
starts again from nothing.

Two invariants are pinned here because losing either is silent:

* `store: false` on every request. The transcript is ours — it is what the audit
  rows, the trimming and the replay are built on — and a copy on OpenAI's side
  is a second history nobody governs.
* only function tools are ever sent. A hosted tool (web search, code
  interpreter, file search, computer use, a remote MCP server) runs inside
  OpenAI, which means it passes no permission check, touches no capability
  matrix and leaves no audit row. That is the whole product, bypassed.
"""

import json
from unittest.mock import patch

import frappe
import requests

from frappe_agents.runner.providers import (
	RESPONSES_MAX_OUTPUT_TOKENS,
	RESPONSES_REASONING_MARKER,
	ProviderError,
	# The hosted-tool guard has no public route: every schema this app builds is
	# a function tool, which is exactly the point of it. It is tested where it
	# lives, because the day it stops holding is the day someone adds one.
	_assert_function_tools_only,
	call_model,
	call_model_stream,
)
from frappe_agents.runner.run import execute_run
from frappe_agents.tests.fixtures import (
	AGENT,
	PROFILE,
	PROVIDER,
	RESTRICTED_USER,
	TICKET_ALPHA,
	TICKET_DT,
	AgentTestCase,
	make_run,
	run_events,
	tool_calls_for,
)
from frappe_agents.tests.test_provider_streams import (
	FakeStream,
	item_added,
	item_done,
	responses_done,
	responses_frame,
	sse,
)

MESSAGES = [{"role": "user", "content": "How many tickets are open?"}]

HOSTED_TOOLS = (
	{"type": "web_search"},
	{"type": "web_search_preview"},
	{"type": "file_search", "vector_store_ids": ["vs_1"]},
	{"type": "code_interpreter", "container": {"type": "auto"}},
	{"type": "computer_use_preview"},
	{"type": "image_generation"},
	{"type": "local_shell"},
	{"type": "mcp", "server_label": "anything", "server_url": "https://example.com/mcp"},
)


def reasoning_item(text: str = "Counting open tickets.", encrypted: str = "ENC") -> dict:
	return {
		"id": "rs_1",
		"type": "reasoning",
		"summary": [{"type": "summary_text", "text": text}],
		"encrypted_content": encrypted,
		"status": "completed",
	}


def packed(item: dict) -> str:
	"""One reasoning item as the thinking block's signature carries it."""
	return f"{RESPONSES_REASONING_MARKER}{json.dumps(item, separators=(',', ':'))}"


def items_of(payload: dict, kind: str) -> list[dict]:
	return [item for item in payload["input"] if item.get("type") == kind]


class ResponsesCase(AgentTestCase):
	"""A test whose provider speaks Responses rather than chat completions."""

	def setUp(self) -> None:
		super().setUp()
		self.use_responses_provider()

	def use_responses_provider(self, base_url: str | None = None) -> None:
		values = {"provider_type": "OpenAI Responses"}
		if base_url is not None:
			values["base_url"] = base_url
		frappe.db.set_value("LLM Provider", PROVIDER, values, update_modified=False)
		frappe.clear_document_cache("LLM Provider", PROVIDER)
		self.addCleanup(frappe.clear_document_cache, "LLM Provider", PROVIDER)

	def sent(self, messages: list[dict], tools: list[dict] | None = None, stream: bool = True):
		"""Make one call against a stubbed socket and return what went out."""
		response = FakeStream([sse(responses_done())] if stream else [])
		response.json = lambda: {"output": [], "usage": {}}

		with patch("frappe_agents.runner.providers.requests.post", return_value=response) as post:
			if stream:
				list(call_model_stream(PROFILE, messages, tools))
			else:
				call_model(PROFILE, messages, tools)
		return post.call_args


class TestResponsesRequest(ResponsesCase):
	def test_every_request_is_stateless_and_asks_for_encrypted_reasoning(self):
		"""`store: false` is not a setting. It is the shape of the integration."""
		for stream in (True, False):
			with self.subTest(stream=stream):
				payload = self.sent(MESSAGES, stream=stream).kwargs["json"]
				self.assertIs(payload["store"], False)
				self.assertIn("reasoning.encrypted_content", payload["include"])
				self.assertEqual(payload["model"], "fa-test-model")
				self.assertEqual(payload["max_output_tokens"], RESPONSES_MAX_OUTPUT_TOKENS)
				self.assertEqual(payload.get("stream"), True if stream else None)
				# Nothing from the chat-completions wire leaks across.
				self.assertNotIn("messages", payload)
				self.assertNotIn("stream_options", payload)
				self.assertNotIn("previous_response_id", payload)

	def test_the_url_carries_exactly_one_version_segment(self):
		"""The seeded base already ends in /v1, and /v1/v1 is a 404 nobody expects."""
		for base_url, expected in (
			("http://localhost:1/v1", "http://localhost:1/v1/responses"),
			("http://localhost:1/v1/", "http://localhost:1/v1/responses"),
			("http://localhost:1", "http://localhost:1/v1/responses"),
			("http://localhost:1/openai/v1", "http://localhost:1/openai/v1/responses"),
		):
			with self.subTest(base_url=base_url):
				self.use_responses_provider(base_url=base_url)
				self.assertEqual(self.sent(MESSAGES).args[0], expected)

	def test_the_system_prompt_becomes_instructions_and_never_an_item(self):
		messages = [
			{"role": "system", "content": "You are careful."},
			{"role": "system", "content": "You are brief."},
			*MESSAGES,
		]
		payload = self.sent(messages).kwargs["json"]

		self.assertEqual(payload["instructions"], "You are careful.\n\nYou are brief.")
		self.assertEqual([item["type"] for item in payload["input"]], ["message"])
		self.assertNotIn("system", [item.get("role") for item in payload["input"]])

	def test_a_turn_with_a_tool_becomes_a_call_and_an_output_paired_by_call_id(self):
		messages = [
			{"role": "system", "content": "You are careful."},
			{"role": "user", "content": "How many tickets are open?"},
			{
				"role": "assistant",
				"content": "Let me look.",
				"tool_calls": [{"id": "call_a", "name": "search_documents", "args": {"doctype": TICKET_DT}}],
			},
			{"role": "tool", "tool_call_id": "call_a", "content": '{"ok": true, "rows": 3}'},
			{"role": "assistant", "content": "Three."},
		]
		payload = self.sent(messages).kwargs["json"]

		self.assertEqual(
			[item["type"] for item in payload["input"]],
			["message", "message", "function_call", "function_call_output", "message"],
		)

		user, assistant = payload["input"][0], payload["input"][1]
		self.assertEqual(
			user,
			{
				"type": "message",
				"role": "user",
				"content": [{"type": "input_text", "text": "How many tickets are open?"}],
			},
		)
		# The two sides use different content part types and each rejects the other's.
		self.assertEqual(assistant["content"][0]["type"], "output_text")

		call = items_of(payload, "function_call")[0]
		self.assertEqual(call["call_id"], "call_a")
		self.assertEqual(call["name"], "search_documents")
		# Arguments travel as a JSON string, not as an object.
		self.assertEqual(json.loads(call["arguments"]), {"doctype": TICKET_DT})

		output = items_of(payload, "function_call_output")[0]
		self.assertEqual(output["call_id"], "call_a")
		self.assertEqual(output["output"], '{"ok": true, "rows": 3}')

	def test_an_assistant_turn_that_only_asked_for_a_tool_sends_no_empty_message(self):
		messages = [
			*MESSAGES,
			{
				"role": "assistant",
				"content": "",
				"tool_calls": [{"id": "call_a", "name": "search_documents", "args": {}}],
			},
			{"role": "tool", "tool_call_id": "call_a", "content": "{}"},
		]
		payload = self.sent(messages).kwargs["json"]

		self.assertEqual(
			[item["type"] for item in payload["input"]],
			["message", "function_call", "function_call_output"],
		)

	def test_tools_are_the_flat_function_shape(self):
		schemas = [
			{
				"name": "search_documents",
				"description": "Find documents.",
				"args_schema": {"type": "object", "properties": {"doctype": {"type": "string"}}},
			},
			{"name": "read_document"},
		]
		payload = self.sent(MESSAGES, schemas).kwargs["json"]

		# Flat: name and parameters at the top level. Chat completions nests both
		# under "function" and the two are not interchangeable.
		self.assertEqual(
			payload["tools"][0],
			{
				"type": "function",
				"name": "search_documents",
				"description": "Find documents.",
				"parameters": {"type": "object", "properties": {"doctype": {"type": "string"}}},
			},
		)
		for tool in payload["tools"]:
			self.assertNotIn("function", tool)
		self.assertEqual(payload["tools"][1]["parameters"], {"type": "object", "properties": {}})

	def test_a_hosted_tool_is_refused_before_anything_can_be_sent(self):
		"""The chokepoint only holds if every tool runs on this side of it."""
		for tool in HOSTED_TOOLS:
			with self.subTest(tool=tool["type"]):
				with self.assertRaises(ProviderError) as caught:
					_assert_function_tools_only({"tools": [{"type": "function", "name": "ok"}, tool]})
				self.assertIn(tool["type"], str(caught.exception))
				self.assertIn("audit", str(caught.exception))

		# A tool with no type at all is not a loophole either.
		with self.assertRaises(ProviderError):
			_assert_function_tools_only({"tools": [{"name": "sneaky"}]})

		# And the shape the app actually builds passes.
		_assert_function_tools_only({"tools": [{"type": "function", "name": "search_documents"}]})

	def test_reasoning_is_replayed_verbatim_beside_the_call_it_led_to(self):
		item = reasoning_item()
		messages = [
			*MESSAGES,
			{
				"role": "assistant",
				"content": "",
				"thinking": [{"thinking": "Counting open tickets.", "signature": packed(item)}],
				"tool_calls": [{"id": "call_a", "name": "search_documents", "args": {}}],
			},
			{"role": "tool", "tool_call_id": "call_a", "content": "{}"},
		]
		payload = self.sent(messages).kwargs["json"]

		self.assertEqual(
			[entry["type"] for entry in payload["input"]],
			["message", "reasoning", "function_call", "function_call_output"],
		)
		# Byte for byte what came back, encrypted content and id included — a
		# reconstruction would not decrypt.
		self.assertEqual(items_of(payload, "reasoning")[0], item)

	def test_a_signature_that_is_not_a_reasoning_item_is_never_replayed(self):
		"""An Anthropic signature in the transcript would be rejected as an item."""
		for signature in (
			"ErUBCkYIBBgCIkDx0h",
			f"{RESPONSES_REASONING_MARKER}not json",
			f"{RESPONSES_REASONING_MARKER}[1,2,3]",
			f"{RESPONSES_REASONING_MARKER}" + json.dumps({"type": "message"}),
			None,
		):
			with self.subTest(signature=str(signature)[:24]):
				messages = [
					*MESSAGES,
					{
						"role": "assistant",
						"content": "",
						"thinking": [{"thinking": "Hmm.", "signature": signature}],
						"tool_calls": [{"id": "call_a", "name": "search_documents", "args": {}}],
					},
				]
				payload = self.sent(messages).kwargs["json"]
				self.assertEqual(items_of(payload, "reasoning"), [])

	def test_a_reasoning_item_too_big_to_store_is_dropped_not_truncated(self):
		"""Half an encrypted blob decrypts to nothing. Better to think again."""
		huge = packed(reasoning_item(encrypted="E" * (1024 * 1024)))
		messages = [
			*MESSAGES,
			{
				"role": "assistant",
				"content": "",
				"thinking": [{"thinking": "Hmm.", "signature": huge}],
				"tool_calls": [{"id": "call_a", "name": "search_documents", "args": {}}],
			},
		]
		payload = self.sent(messages).kwargs["json"]
		self.assertEqual(items_of(payload, "reasoning"), [])

	def test_reasoning_with_nothing_after_it_is_never_sent_on_its_own(self):
		"""A reasoning item is only valid beside the thing it produced.

		OpenAI refuses an input whose reasoning item is not followed by the output
		item it led to — by name, with the whole request. A turn that thought and
		then said nothing (a run cut short, a transcript trimmed to the wrong
		place) would otherwise replay the reasoning as the last item and 400 every
		later turn in that conversation.
		"""
		item = reasoning_item()
		orphan = {
			"role": "assistant",
			"content": "",
			"thinking": [{"thinking": "", "signature": packed(item)}],
		}
		payload = self.sent([*MESSAGES, orphan]).kwargs["json"]

		self.assertEqual([entry["type"] for entry in payload["input"]], ["message"])
		self.assertEqual(items_of(payload, "reasoning"), [])

		# It still travels when the turn did produce something, either kind.
		for tail in (
			{**orphan, "content": "Three."},
			{**orphan, "tool_calls": [{"id": "call_a", "name": "search_documents", "args": {}}]},
		):
			with self.subTest(produced="text" if tail.get("content") else "call"):
				payload = self.sent([*MESSAGES, tail]).kwargs["json"]
				self.assertEqual(items_of(payload, "reasoning"), [item])
				self.assertNotEqual(payload["input"][-1]["type"], "reasoning")


class TestResponsesAnswer(ResponsesCase):
	"""The whole-answer path: `output` items in, text and tool calls out."""

	def answer(self, data: dict) -> dict:
		response = FakeStream([], text="")
		response.json = lambda: data
		with patch("frappe_agents.runner.providers.requests.post", return_value=response):
			return call_model(PROFILE, MESSAGES)

	def test_output_items_become_text_tool_calls_and_tokens(self):
		result = self.answer(
			{
				"id": "resp_1",
				"status": "completed",
				"output": [
					reasoning_item(),
					{
						"type": "message",
						"role": "assistant",
						"content": [
							{"type": "output_text", "text": "Three "},
							{"type": "output_text", "text": "are open."},
						],
					},
					{
						"type": "function_call",
						"id": "fc_1",
						"call_id": "call_a",
						"name": "search_documents",
						"arguments": '{"doctype":"FA Test Ticket"}',
					},
				],
				"usage": {"input_tokens": 41, "output_tokens": 7, "total_tokens": 48},
			}
		)

		self.assertEqual(result["text"], "Three are open.")
		self.assertEqual(
			result["tool_calls"],
			[{"id": "call_a", "name": "search_documents", "args": {"doctype": "FA Test Ticket"}}],
		)
		self.assertEqual(result["tokens_in"], 41)
		self.assertEqual(result["tokens_out"], 7)

	def test_an_answer_with_no_message_item_has_no_text(self):
		result = self.answer({"output": [reasoning_item()], "usage": {}})
		self.assertIsNone(result["text"])
		self.assertEqual(result["tool_calls"], [])


class TestResponsesErrors(ResponsesCase):
	"""What a refusal may say. Never the key, never a whole megabyte of it."""

	def test_a_refusal_is_bounded_and_never_shows_the_key(self):
		for status in (400, 401, 429, 500):
			for stream in (True, False):
				with self.subTest(status=status, stream=stream):
					response = FakeStream(
						[],
						status_code=status,
						text=f"rejected key fa-test-key {'y' * 40_000}",
					)
					with patch("frappe_agents.runner.providers.requests.post", return_value=response):
						with self.assertRaises(ProviderError) as caught:
							if stream:
								list(call_model_stream(PROFILE, MESSAGES))
							else:
								call_model(PROFILE, MESSAGES)

					message = str(caught.exception)
					self.assertIn(str(status), message)
					self.assertIn("/v1/responses", message)
					self.assertNotIn("fa-test-key", message)
					self.assertIn("***", message)
					self.assertTrue(response.closed)

	def test_a_transport_failure_never_shows_the_key(self):
		broken = requests.RequestException("could not reach host with key fa-test-key")
		with patch("frappe_agents.runner.providers.requests.post", side_effect=broken):
			with self.assertRaises(ProviderError) as caught:
				list(call_model_stream(PROFILE, MESSAGES))
		self.assertNotIn("fa-test-key", str(caught.exception))


class TestResponsesLoop(ResponsesCase):
	"""One whole run over the Responses wire, with only the socket stubbed.

	The loop, the executor, the permission check and the audit rows are all the
	real ones. What this proves is that a tool call made over this wire executes
	through the same chokepoint and leaves the same row as one made over either
	of the others — and that the next request carries the call, its result and
	the reasoning that led to it.
	"""

	def bodies(self) -> list[bytes]:
		reasoning = reasoning_item(text="The ticket table will have it.")
		call = {
			"id": "fc_1",
			"type": "function_call",
			"call_id": "call_a",
			"name": "search_documents",
			"arguments": json.dumps({"doctype": TICKET_DT, "fields": ["name"]}),
		}
		message = {"id": "msg_1", "type": "message", "role": "assistant", "content": []}

		asking = sse(
			responses_frame("response.created", response={"id": "resp_1", "status": "in_progress"}),
			item_added(0, dict(reasoning, summary=[], encrypted_content="PART")),
			responses_frame(
				"response.reasoning_summary_text.delta",
				output_index=0,
				summary_index=0,
				delta="The ticket table will have it.",
			),
			item_done(0, reasoning),
			item_added(1, dict(call, arguments="")),
			responses_frame(
				"response.function_call_arguments.delta", output_index=1, delta=call["arguments"]
			),
			item_done(1, call),
			responses_done(tokens_in=120, tokens_out=30),
		)
		answering = sse(
			item_added(0, message),
			responses_frame("response.output_text.delta", output_index=0, delta="One ticket is open."),
			item_done(0, message),
			responses_done(tokens_in=200, tokens_out=8),
		)
		return [asking, answering]

	def test_a_tool_call_over_this_wire_runs_through_the_chokepoint(self):
		run = make_run(effective_user=RESTRICTED_USER, agent=AGENT)
		asking, answering = self.bodies()

		with patch(
			"frappe_agents.runner.providers.requests.post",
			side_effect=[FakeStream([asking]), FakeStream([answering])],
		) as post:
			execute_run(run.name)

		self.assertEqual(post.call_count, 2)
		self.assertEqual(frappe.db.get_value("Agent Run", run.name, "status"), "Completed")

		# The audit row is the same shape the other two wires leave behind.
		calls = tool_calls_for(run.name)
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].tool, "search_documents")
		self.assertEqual(calls[0].outcome, "Success")
		self.assertIn(TICKET_DT, calls[0].args_json)
		self.assertIn(TICKET_ALPHA, calls[0].docs_touched)

		# Tokens from both calls were counted.
		self.assertEqual(frappe.db.get_value("Agent Run", run.name, "tokens_in"), 320)
		self.assertEqual(frappe.db.get_value("Agent Run", run.name, "tokens_out"), 38)

		types = [event.get("type") for event in run_events(run.name)]
		self.assertIn("tool_execution_end", types)

	def test_the_second_request_carries_the_call_its_result_and_the_reasoning(self):
		run = make_run(effective_user=RESTRICTED_USER, agent=AGENT)
		asking, answering = self.bodies()

		with patch(
			"frappe_agents.runner.providers.requests.post",
			side_effect=[FakeStream([asking]), FakeStream([answering])],
		) as post:
			execute_run(run.name)

		second = post.call_args_list[1].kwargs["json"]
		self.assertIs(second["store"], False)
		self.assertEqual(
			[item["type"] for item in second["input"]][-3:],
			["reasoning", "function_call", "function_call_output"],
		)

		# Verbatim, as it arrived on the first call.
		self.assertEqual(
			items_of(second, "reasoning")[0], reasoning_item(text="The ticket table will have it.")
		)

		call = items_of(second, "function_call")[0]
		output = items_of(second, "function_call_output")[0]
		self.assertEqual(call["call_id"], "call_a")
		self.assertEqual(output["call_id"], "call_a")
		self.assertIn(TICKET_ALPHA, output["output"])

		# The tools offered are still only ours, on the turn after a tool ran.
		self.assertTrue(second["tools"])
		for tool in second["tools"]:
			self.assertEqual(tool["type"], "function")
