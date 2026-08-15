# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The three SSE parsers, fed canned bytes.

Nothing here touches the network. A stream is a list of byte chunks, and the
chunk boundaries are the point: the network decides where a packet ends, and it
happily ends one in the middle of a word, in the middle of a CRLF pair, or in the
middle of a multi-byte character. A parser that only works on tidy whole lines
works in a test and drops text in production, so most of these tests re-run the
same stream cut one byte at a time.

The transport tests below the parsers patch `requests.post` and read the request
that would have gone out — the streaming flag, the two-part timeout, and the
promise that a key never reaches an error message.
"""

import json
from unittest.mock import patch

import frappe
import requests

from frappe_agents.runner.providers import (
	ERROR_BODY_BYTES,
	ERROR_BODY_LIMIT,
	RESPONSES_REASONING_MARKER,
	SSE_LINE_LIMIT,
	STREAM_CONNECT_TIMEOUT,
	STREAM_IDLE_TIMEOUT,
	TOOL_ARGUMENTS_LIMIT,
	ProviderError,
	call_model,
	call_model_stream,
	parse_anthropic_stream,
	parse_openai_stream,
	parse_responses_stream,
)
from frappe_agents.tests.fixtures import PROFILE, PROVIDER, AgentTestCase

MESSAGES = [{"role": "user", "content": "How many tickets are open?"}]


# --- canned streams ----------------------------------------------------------


def sse(*frames: str, newline: str = "\n") -> bytes:
	"""An SSE body. Each frame is already `event:`/`data:` lines, minus the blank."""
	body = "".join(f"{frame}\n\n" for frame in frames)
	return body.replace("\n", newline).encode()


def dumped(payload: dict) -> str:
	# `ensure_ascii=False` on purpose: an escaped body would carry no multi-byte
	# character, and the tests that cut a chunk through one would prove nothing.
	return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def data_frame(payload: dict | str) -> str:
	return f"data: {payload if isinstance(payload, str) else dumped(payload)}"


def named_frame(event: str, payload: dict) -> str:
	return f"event: {event}\ndata: {dumped(payload)}"


def responses_frame(kind: str, **fields) -> str:
	"""One semantic Responses event, named in the SSE header and in the body.

	The wire says the type twice and the parser reads the body, so both are set:
	a test that only sets one would pass against a parser reading the other.
	"""
	return named_frame(kind, {"type": kind, **fields})


def item_added(output_index: int, item: dict) -> str:
	return responses_frame("response.output_item.added", output_index=output_index, item=item)


def item_done(output_index: int, item: dict) -> str:
	return responses_frame("response.output_item.done", output_index=output_index, item=item)


def responses_done(
	status: str = "completed",
	tokens_in: int = 0,
	tokens_out: int = 0,
	incomplete_reason: str | None = None,
	error: dict | None = None,
) -> str:
	"""The whole response object that ends the stream, as each status sends it."""
	kind = {
		"completed": "response.completed",
		"incomplete": "response.incomplete",
		"failed": "response.failed",
	}[status]
	response: dict = {"id": "resp_1", "object": "response", "status": status}
	if tokens_in or tokens_out:
		response["usage"] = {
			"input_tokens": tokens_in,
			"output_tokens": tokens_out,
			"total_tokens": tokens_in + tokens_out,
		}
	if incomplete_reason:
		response["incomplete_details"] = {"reason": incomplete_reason}
	if error:
		response["error"] = error
	return responses_frame(kind, response=response)


def openai_frame(delta: dict, finish_reason: str | None = None) -> str:
	return data_frame(
		{
			"id": "chatcmpl-1",
			"object": "chat.completion.chunk",
			"choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
		}
	)


def one_byte_at_a_time(body: bytes) -> list[bytes]:
	"""The cruellest split there is: every chunk boundary, all at once."""
	return [body[index : index + 1] for index in range(len(body))]


def cut_at(body: bytes, *positions: int) -> list[bytes]:
	chunks = []
	previous = 0
	for position in (*positions, len(body)):
		chunks.append(body[previous:position])
		previous = position
	return [chunk for chunk in chunks if chunk]


def of_type(chunks: list[dict], kind: str) -> list[dict]:
	return [chunk for chunk in chunks if chunk["type"] == kind]


def joined(chunks: list[dict], kind: str) -> str:
	return "".join(chunk["text"] for chunk in of_type(chunks, kind))


def chunk_types(chunks: list[dict]) -> list[str]:
	return [chunk["type"] for chunk in chunks]


class TestOpenAIStream(AgentTestCase):
	"""The OpenAI-compatible wire format, which marks no block boundaries at all."""

	def test_text_arrives_as_one_block_however_the_bytes_are_cut(self):
		body = sse(
			openai_frame({"role": "assistant", "content": ""}),
			openai_frame({"content": "Three "}),
			openai_frame({"content": "tickets "}),
			openai_frame({"content": "are open."}),
			openai_frame({}, finish_reason="stop"),
			"data: [DONE]",
		)

		for label, stream in (("whole", [body]), ("byte by byte", one_byte_at_a_time(body))):
			with self.subTest(label):
				chunks = list(parse_openai_stream(stream, model="fa-test-model"))
				self.assertEqual(chunks[0], {"type": "message_start", "model": "fa-test-model"})
				self.assertEqual(joined(chunks, "text_delta"), "Three tickets are open.")
				self.assertEqual(len(of_type(chunks, "text_start")), 1)
				self.assertEqual(
					of_type(chunks, "text_end")[0],
					{"type": "text_end", "index": 0, "text": "Three tickets are open."},
				)
				self.assertEqual(chunks[-1], {"type": "message_end", "reason": "stop"})

	def test_a_chunk_cut_through_a_character_loses_nothing(self):
		"""A multi-byte character split across two packets is still one character."""
		answer = "Café ✅ مرحبا"
		body = sse(openai_frame({"content": answer}), openai_frame({}, finish_reason="stop"))

		# Every single cut position, which includes every byte inside every
		# multi-byte character in the payload.
		for position in range(1, len(body)):
			chunks = list(parse_openai_stream(cut_at(body, position)))
			self.assertEqual(joined(chunks, "text_delta"), answer)

	def test_crlf_and_bare_cr_line_endings_parse(self):
		frames = (openai_frame({"content": "Hi"}), openai_frame({}, finish_reason="stop"))
		for newline in ("\r\n", "\r", "\n"):
			with self.subTest(repr(newline)):
				body = sse(*frames, newline=newline)
				for stream in ([body], one_byte_at_a_time(body)):
					chunks = list(parse_openai_stream(stream))
					self.assertEqual(joined(chunks, "text_delta"), "Hi")
					self.assertEqual(chunks[-1], {"type": "message_end", "reason": "stop"})

	def test_keepalive_comments_are_not_content(self):
		"""OpenRouter sends a comment line every few seconds while a model warms up."""
		body = sse(
			": OPENROUTER PROCESSING",
			openai_frame({"content": "Hi"}),
			": OPENROUTER PROCESSING",
			openai_frame({}, finish_reason="stop"),
			"data: [DONE]",
		)
		chunks = list(parse_openai_stream([body]))
		self.assertEqual(joined(chunks, "text_delta"), "Hi")
		self.assertEqual(len(of_type(chunks, "text_start")), 1)

	def test_usage_is_reported_when_it_comes(self):
		body = sse(
			openai_frame({"content": "Hi"}),
			openai_frame({}, finish_reason="stop"),
			data_frame({"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 40}}),
			"data: [DONE]",
		)
		chunks = list(parse_openai_stream([body]))
		self.assertEqual(
			of_type(chunks, "usage")[-1],
			{"type": "usage", "tokens_in": 120, "tokens_out": 40},
		)

	def test_a_stream_with_no_usage_frame_still_ends_cleanly(self):
		"""Plenty of endpoints do not know `stream_options`. That is not an error."""
		body = sse(openai_frame({"content": "Hi"}), openai_frame({}, finish_reason="stop"), "data: [DONE]")
		chunks = list(parse_openai_stream([body]))
		self.assertEqual(of_type(chunks, "usage"), [])
		self.assertEqual(chunks[-1], {"type": "message_end", "reason": "stop"})

	def test_nothing_after_done_is_read(self):
		body = sse(
			openai_frame({"content": "Hi"}),
			"data: [DONE]",
			openai_frame({"content": " and more"}),
		)
		chunks = list(parse_openai_stream([body]))
		self.assertEqual(joined(chunks, "text_delta"), "Hi")

	def test_tool_arguments_are_reassembled_by_index(self):
		"""Two calls at once, fragments interleaved, ids sent only in the openers."""
		body = sse(
			openai_frame(
				{
					"tool_calls": [
						{
							"index": 0,
							"id": "call_a",
							"type": "function",
							"function": {"name": "search_documents", "arguments": ""},
						}
					]
				}
			),
			openai_frame(
				{
					"tool_calls": [
						{
							"index": 1,
							"id": "call_b",
							"type": "function",
							"function": {"name": "read_document"},
						}
					]
				}
			),
			openai_frame({"tool_calls": [{"index": 0, "function": {"arguments": '{"doctype":'}}]}),
			openai_frame({"tool_calls": [{"index": 1, "function": {"arguments": '{"name":"ORD'}}]}),
			openai_frame({"tool_calls": [{"index": 0, "function": {"arguments": ' "Task"}'}}]}),
			openai_frame({"tool_calls": [{"index": 1, "function": {"arguments": '-1"}'}}]}),
			openai_frame({}, finish_reason="tool_calls"),
			"data: [DONE]",
		)

		for stream in ([body], one_byte_at_a_time(body)):
			chunks = list(parse_openai_stream(stream))
			ends = of_type(chunks, "toolcall_end")
			self.assertEqual([end["id"] for end in ends], ["call_a", "call_b"])
			self.assertEqual([end["name"] for end in ends], ["search_documents", "read_document"])
			self.assertEqual(ends[0]["args"], {"doctype": "Task"})
			self.assertEqual(ends[1]["args"], {"name": "ORD-1"})
			self.assertEqual([end["index"] for end in ends], [0, 1])
			self.assertEqual(chunks[-1], {"type": "message_end", "reason": "toolUse"})

	def test_a_tool_call_with_no_index_still_reassembles(self):
		body = sse(
			openai_frame(
				{
					"tool_calls": [
						{"id": "call_a", "function": {"name": "search_documents", "arguments": '{"d'}}
					]
				}
			),
			openai_frame({"tool_calls": [{"function": {"arguments": 'octype": "Task"}'}}]}),
			openai_frame({}, finish_reason="tool_calls"),
		)
		chunks = list(parse_openai_stream([body]))
		ends = of_type(chunks, "toolcall_end")
		self.assertEqual(len(ends), 1)
		self.assertEqual(ends[0]["args"], {"doctype": "Task"})

	def test_unparseable_arguments_end_as_an_empty_object(self):
		"""The model's mistake to be told about, not the run's to die on."""
		body = sse(
			openai_frame(
				{
					"tool_calls": [
						{"index": 0, "id": "call_a", "function": {"name": "x", "arguments": "{oops"}}
					]
				}
			),
			openai_frame({}, finish_reason="tool_calls"),
		)
		ends = of_type(list(parse_openai_stream([body])), "toolcall_end")
		self.assertEqual(ends[0]["args"], {})
		self.assertEqual(ends[0]["arguments"], "{oops")

	def test_reasoning_becomes_thinking_and_closes_before_the_answer(self):
		"""OpenRouter's field name. The block must close when the answer starts."""
		body = sse(
			openai_frame({"reasoning": "Let me "}),
			openai_frame({"reasoning": "count them."}),
			openai_frame({"content": "Three."}),
			openai_frame({}, finish_reason="stop"),
			"data: [DONE]",
		)
		chunks = list(parse_openai_stream([body]))
		self.assertEqual(
			chunk_types(chunks),
			[
				"message_start",
				"thinking_start",
				"thinking_delta",
				"thinking_delta",
				"thinking_end",
				"text_start",
				"text_delta",
				"text_end",
				"message_end",
			],
		)
		thinking_end = of_type(chunks, "thinking_end")[0]
		self.assertEqual(thinking_end["text"], "Let me count them.")
		self.assertEqual(thinking_end["index"], 0)
		self.assertIsNone(thinking_end["signature"])
		self.assertEqual(of_type(chunks, "text_start")[0]["index"], 1)

	def test_deepseeks_field_name_is_read_too(self):
		body = sse(openai_frame({"reasoning_content": "Hmm."}), openai_frame({}, finish_reason="stop"))
		chunks = list(parse_openai_stream([body]))
		self.assertEqual(joined(chunks, "thinking_delta"), "Hmm.")

	def test_a_stream_that_stops_mid_block_still_closes_it(self):
		"""The connection died. The half-written answer is still the answer."""
		body = sse(openai_frame({"content": "Three tick"}))
		chunks = list(parse_openai_stream([body]))
		self.assertEqual(
			of_type(chunks, "text_end")[0],
			{"type": "text_end", "index": 0, "text": "Three tick"},
		)
		self.assertEqual(chunks[-1], {"type": "message_end", "reason": "stop"})

	def test_a_frame_that_will_not_parse_is_skipped_not_fatal(self):
		body = sse(
			openai_frame({"content": "Hi"}),
			"data: {this is not json",
			openai_frame({"content": " there"}),
			openai_frame({}, finish_reason="stop"),
		)
		chunks = list(parse_openai_stream([body]))
		self.assertEqual(joined(chunks, "text_delta"), "Hi there")

	def test_an_error_inside_a_200_body_raises(self):
		body = sse(data_frame({"error": {"message": "upstream is overloaded", "type": "server_error"}}))
		with self.assertRaises(ProviderError) as caught:
			list(parse_openai_stream([body]))
		self.assertIn("upstream is overloaded", str(caught.exception))

	def test_a_truncated_answer_is_reported_as_length(self):
		body = sse(openai_frame({"content": "Three"}), openai_frame({}, finish_reason="length"))
		chunks = list(parse_openai_stream([body]))
		self.assertEqual(chunks[-1], {"type": "message_end", "reason": "length"})


class TestAnthropicStream(AgentTestCase):
	"""The Anthropic wire format, which states every block boundary."""

	def text_stream(self) -> bytes:
		return sse(
			named_frame(
				"message_start",
				{
					"type": "message_start",
					"message": {"model": "claude-x", "usage": {"input_tokens": 120, "output_tokens": 1}},
				},
			),
			named_frame(
				"content_block_start",
				{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
			),
			named_frame(
				"content_block_delta",
				{
					"type": "content_block_delta",
					"index": 0,
					"delta": {"type": "text_delta", "text": "Three "},
				},
			),
			named_frame(
				"content_block_delta",
				{
					"type": "content_block_delta",
					"index": 0,
					"delta": {"type": "text_delta", "text": "are open."},
				},
			),
			named_frame("content_block_stop", {"type": "content_block_stop", "index": 0}),
			named_frame(
				"message_delta",
				{
					"type": "message_delta",
					"delta": {"stop_reason": "end_turn"},
					"usage": {"output_tokens": 42},
				},
			),
			named_frame("message_stop", {"type": "message_stop"}),
		)

	def test_text_and_usage_however_the_bytes_are_cut(self):
		body = self.text_stream()
		for label, stream in (("whole", [body]), ("byte by byte", one_byte_at_a_time(body))):
			with self.subTest(label):
				chunks = list(parse_anthropic_stream(stream, model="claude-x"))
				self.assertEqual(chunks[0], {"type": "message_start", "model": "claude-x"})
				self.assertEqual(joined(chunks, "text_delta"), "Three are open.")
				self.assertEqual(of_type(chunks, "text_end")[0]["text"], "Three are open.")
				self.assertEqual(
					of_type(chunks, "usage")[-1],
					{"type": "usage", "tokens_in": 120, "tokens_out": 42},
				)
				self.assertEqual(chunks[-1], {"type": "message_end", "reason": "stop"})

	def test_a_chunk_cut_through_a_character_loses_nothing(self):
		"""The same cruelty as the other format: a character split across packets."""
		answer = "Café ✅ مرحبا"
		body = sse(
			named_frame(
				"content_block_start",
				{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
			),
			named_frame(
				"content_block_delta",
				{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": answer}},
			),
			named_frame("content_block_stop", {"type": "content_block_stop", "index": 0}),
			named_frame("message_stop", {"type": "message_stop"}),
		)

		for position in range(1, len(body)):
			chunks = list(parse_anthropic_stream(cut_at(body, position)))
			self.assertEqual(joined(chunks, "text_delta"), answer)

	def test_crlf_and_bare_cr_line_endings_parse(self):
		for newline in ("\r\n", "\r", "\n"):
			with self.subTest(repr(newline)):
				body = self.text_stream().decode().replace("\n", newline).encode()
				for stream in ([body], one_byte_at_a_time(body)):
					chunks = list(parse_anthropic_stream(stream))
					self.assertEqual(joined(chunks, "text_delta"), "Three are open.")
					self.assertEqual(
						of_type(chunks, "usage")[-1],
						{"type": "usage", "tokens_in": 120, "tokens_out": 42},
					)
					self.assertEqual(chunks[-1], {"type": "message_end", "reason": "stop"})

	def test_ping_frames_are_ignored(self):
		body = sse(
			named_frame("ping", {"type": "ping"}),
			named_frame(
				"content_block_start",
				{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
			),
			named_frame("ping", {"type": "ping"}),
			named_frame(
				"content_block_delta",
				{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi"}},
			),
			named_frame("content_block_stop", {"type": "content_block_stop", "index": 0}),
			named_frame("message_stop", {"type": "message_stop"}),
		)
		chunks = list(parse_anthropic_stream([body]))
		self.assertEqual(joined(chunks, "text_delta"), "Hi")
		self.assertEqual(len(of_type(chunks, "text_start")), 1)

	def test_thinking_carries_its_signature_out_of_the_fragments(self):
		"""The signature is verified byte for byte on the next turn. It must be exact."""
		body = sse(
			named_frame(
				"content_block_start",
				{
					"type": "content_block_start",
					"index": 0,
					"content_block": {"type": "thinking", "thinking": ""},
				},
			),
			named_frame(
				"content_block_delta",
				{
					"type": "content_block_delta",
					"index": 0,
					"delta": {"type": "thinking_delta", "thinking": "Let me "},
				},
			),
			named_frame(
				"content_block_delta",
				{
					"type": "content_block_delta",
					"index": 0,
					"delta": {"type": "thinking_delta", "thinking": "count."},
				},
			),
			named_frame(
				"content_block_delta",
				{
					"type": "content_block_delta",
					"index": 0,
					"delta": {"type": "signature_delta", "signature": "EqQBCgIY"},
				},
			),
			named_frame(
				"content_block_delta",
				{
					"type": "content_block_delta",
					"index": 0,
					"delta": {"type": "signature_delta", "signature": "AhgCIkA="},
				},
			),
			named_frame("content_block_stop", {"type": "content_block_stop", "index": 0}),
			named_frame(
				"content_block_start",
				{"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
			),
			named_frame(
				"content_block_delta",
				{
					"type": "content_block_delta",
					"index": 1,
					"delta": {"type": "text_delta", "text": "Three."},
				},
			),
			named_frame("content_block_stop", {"type": "content_block_stop", "index": 1}),
			named_frame("message_stop", {"type": "message_stop"}),
		)

		for stream in ([body], one_byte_at_a_time(body)):
			chunks = list(parse_anthropic_stream(stream))
			self.assertEqual(
				chunk_types(chunks),
				[
					"message_start",
					"thinking_start",
					"thinking_delta",
					"thinking_delta",
					"thinking_end",
					"text_start",
					"text_delta",
					"text_end",
					"message_end",
				],
			)
			thinking_end = of_type(chunks, "thinking_end")[0]
			self.assertEqual(thinking_end["text"], "Let me count.")
			self.assertEqual(thinking_end["signature"], "EqQBCgIYAhgCIkA=")
			self.assertFalse(thinking_end["redacted"])
			self.assertEqual(of_type(chunks, "text_start")[0]["index"], 1)

	def test_a_redacted_thinking_block_keeps_its_ciphertext(self):
		body = sse(
			named_frame(
				"content_block_start",
				{
					"type": "content_block_start",
					"index": 0,
					"content_block": {"type": "redacted_thinking", "data": "EvgBCkYIAxg"},
				},
			),
			named_frame("content_block_stop", {"type": "content_block_stop", "index": 0}),
			named_frame("message_stop", {"type": "message_stop"}),
		)
		chunks = list(parse_anthropic_stream([body]))
		end = of_type(chunks, "thinking_end")[0]
		self.assertTrue(end["redacted"])
		self.assertEqual(end["text"], "EvgBCkYIAxg")

	def test_tool_input_is_reassembled_from_json_fragments(self):
		body = sse(
			named_frame(
				"content_block_start",
				{
					"type": "content_block_start",
					"index": 0,
					"content_block": {
						"type": "tool_use",
						"id": "toolu_1",
						"name": "search_documents",
						"input": {},
					},
				},
			),
			named_frame(
				"content_block_delta",
				{
					"type": "content_block_delta",
					"index": 0,
					"delta": {"type": "input_json_delta", "partial_json": '{"doctype"'},
				},
			),
			named_frame(
				"content_block_delta",
				{
					"type": "content_block_delta",
					"index": 0,
					"delta": {"type": "input_json_delta", "partial_json": ': "Task"}'},
				},
			),
			named_frame("content_block_stop", {"type": "content_block_stop", "index": 0}),
			named_frame(
				"message_delta",
				{
					"type": "message_delta",
					"delta": {"stop_reason": "tool_use"},
					"usage": {"output_tokens": 9},
				},
			),
			named_frame("message_stop", {"type": "message_stop"}),
		)

		for stream in ([body], one_byte_at_a_time(body)):
			chunks = list(parse_anthropic_stream(stream))
			self.assertEqual(joined(chunks, "toolcall_delta"), '{"doctype": "Task"}')
			end = of_type(chunks, "toolcall_end")[0]
			self.assertEqual(end["id"], "toolu_1")
			self.assertEqual(end["name"], "search_documents")
			self.assertEqual(end["args"], {"doctype": "Task"})
			self.assertEqual(chunks[-1], {"type": "message_end", "reason": "toolUse"})

	def test_a_tool_call_with_no_arguments_ends_as_an_empty_object(self):
		body = sse(
			named_frame(
				"content_block_start",
				{
					"type": "content_block_start",
					"index": 0,
					"content_block": {
						"type": "tool_use",
						"id": "toolu_1",
						"name": "list_agents",
						"input": {},
					},
				},
			),
			named_frame("content_block_stop", {"type": "content_block_stop", "index": 0}),
			named_frame("message_stop", {"type": "message_stop"}),
		)
		end = of_type(list(parse_anthropic_stream([body])), "toolcall_end")[0]
		self.assertEqual(end["args"], {})

	def test_a_stream_with_no_usage_frame_still_ends_cleanly(self):
		body = sse(
			named_frame(
				"content_block_start",
				{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
			),
			named_frame(
				"content_block_delta",
				{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi"}},
			),
			named_frame("content_block_stop", {"type": "content_block_stop", "index": 0}),
			named_frame("message_stop", {"type": "message_stop"}),
		)
		chunks = list(parse_anthropic_stream([body]))
		self.assertEqual(of_type(chunks, "usage"), [])
		self.assertEqual(chunks[-1], {"type": "message_end", "reason": "stop"})

	def test_a_stream_cut_before_the_block_stops_still_closes_it(self):
		body = sse(
			named_frame(
				"content_block_start",
				{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
			),
			named_frame(
				"content_block_delta",
				{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Three"}},
			),
		)
		chunks = list(parse_anthropic_stream([body]))
		self.assertEqual(of_type(chunks, "text_end")[0]["text"], "Three")
		self.assertEqual(chunks[-1], {"type": "message_end", "reason": "stop"})

	def test_max_tokens_is_reported_as_length(self):
		body = sse(
			named_frame("message_delta", {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}}),
			named_frame("message_stop", {"type": "message_stop"}),
		)
		chunks = list(parse_anthropic_stream([body]))
		self.assertEqual(chunks[-1], {"type": "message_end", "reason": "length"})

	def test_an_error_event_raises(self):
		body = sse(
			named_frame(
				"error", {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
			)
		)
		with self.assertRaises(ProviderError) as caught:
			list(parse_anthropic_stream([body]))
		self.assertIn("Overloaded", str(caught.exception))


class TestResponsesStream(AgentTestCase):
	"""The OpenAI Responses wire format, which names every boundary it has.

	Nothing has to be inferred here — items are announced and closed, and deltas
	say which item they belong to. What still has to be got right is that the
	end of an item is where the truth is: a tool call's arguments are only
	complete there, and a reasoning item's encrypted content may still be a
	fragment when the item opens.
	"""

	def test_text_arrives_as_one_block_however_the_bytes_are_cut(self):
		message = {"id": "msg_1", "type": "message", "role": "assistant", "content": []}
		body = sse(
			responses_frame("response.created", response={"id": "resp_1", "status": "in_progress"}),
			item_added(0, message),
			responses_frame("response.content_part.added", output_index=0, content_index=0),
			responses_frame("response.output_text.delta", output_index=0, delta="Three "),
			responses_frame("response.output_text.delta", output_index=0, delta="tickets "),
			responses_frame("response.output_text.delta", output_index=0, delta="are open."),
			responses_frame("response.output_text.done", output_index=0, text="Three tickets are open."),
			item_done(0, message),
			responses_done(tokens_in=41, tokens_out=7),
		)

		for label, stream in (("whole", [body]), ("byte by byte", one_byte_at_a_time(body))):
			with self.subTest(label):
				chunks = list(parse_responses_stream(stream, model="fa-test-model"))
				self.assertEqual(chunks[0], {"type": "message_start", "model": "fa-test-model"})
				self.assertEqual(joined(chunks, "text_delta"), "Three tickets are open.")
				self.assertEqual(len(of_type(chunks, "text_start")), 1)
				self.assertEqual(
					of_type(chunks, "text_end")[0],
					{"type": "text_end", "index": 0, "text": "Three tickets are open."},
				)
				self.assertEqual(
					of_type(chunks, "usage")[-1],
					{"type": "usage", "tokens_in": 41, "tokens_out": 7},
				)
				self.assertEqual(chunks[-1], {"type": "message_end", "reason": "stop"})

	def test_a_chunk_cut_through_a_character_loses_nothing(self):
		answer = "Café ✅ مرحبا"
		message = {"id": "msg_1", "type": "message", "role": "assistant", "content": []}
		body = sse(
			item_added(0, message),
			responses_frame("response.output_text.delta", output_index=0, delta=answer),
			item_done(0, message),
			responses_done(),
		)

		for position in range(1, len(body)):
			chunks = list(parse_responses_stream(cut_at(body, position)))
			self.assertEqual(joined(chunks, "text_delta"), answer)

	def test_reasoning_becomes_a_thinking_block_carrying_the_item_to_replay(self):
		"""The summary is what a human reads; the signature is what the model gets back."""
		opening = {"id": "rs_1", "type": "reasoning", "summary": [], "encrypted_content": "ENC-PART"}
		finished = {
			"id": "rs_1",
			"type": "reasoning",
			"summary": [{"type": "summary_text", "text": "Counting open tickets."}],
			"encrypted_content": "ENC-WHOLE",
			"status": "completed",
		}
		body = sse(
			item_added(0, opening),
			responses_frame("response.reasoning_summary_part.added", output_index=0, summary_index=0),
			responses_frame(
				"response.reasoning_summary_text.delta", output_index=0, summary_index=0, delta="Counting "
			),
			responses_frame(
				"response.reasoning_summary_text.delta",
				output_index=0,
				summary_index=0,
				delta="open tickets.",
			),
			item_done(0, finished),
			responses_done(),
		)

		for label, stream in (("whole", [body]), ("byte by byte", one_byte_at_a_time(body))):
			with self.subTest(label):
				chunks = list(parse_responses_stream(stream))
				start = of_type(chunks, "thinking_start")[0]
				self.assertEqual(start, {"type": "thinking_start", "index": 0, "redacted": False})

				end = of_type(chunks, "thinking_end")[0]
				self.assertEqual(end["text"], "Counting open tickets.")
				self.assertFalse(end["redacted"])
				# The item that closed the block, not the fragment that opened it:
				# the encrypted content in `.added` may be incomplete.
				self.assertTrue(end["signature"].startswith(RESPONSES_REASONING_MARKER))
				packed = json.loads(end["signature"][len(RESPONSES_REASONING_MARKER) :])
				self.assertEqual(packed, finished)

	def test_a_summary_in_two_parts_is_one_block_with_a_break_between_them(self):
		item = {"id": "rs_1", "type": "reasoning", "summary": [], "encrypted_content": "ENC"}
		body = sse(
			item_added(0, item),
			responses_frame("response.reasoning_summary_part.added", output_index=0, summary_index=0),
			responses_frame(
				"response.reasoning_summary_text.delta", output_index=0, summary_index=0, delta="First."
			),
			responses_frame("response.reasoning_summary_part.added", output_index=0, summary_index=1),
			responses_frame(
				"response.reasoning_summary_text.delta", output_index=0, summary_index=1, delta="Second."
			),
			item_done(0, item),
			responses_done(),
		)

		chunks = list(parse_responses_stream([body]))
		self.assertEqual(len(of_type(chunks, "thinking_start")), 1)
		self.assertEqual(of_type(chunks, "thinking_end")[0]["text"], "First.\n\nSecond.")

	def test_reasoning_text_deltas_are_thinking_too(self):
		"""Some models stream the reasoning itself, not only a summary of it."""
		item = {"id": "rs_1", "type": "reasoning", "summary": [], "encrypted_content": "ENC"}
		body = sse(
			item_added(0, item),
			responses_frame(
				"response.reasoning_text.delta", output_index=0, content_index=0, delta="Step one."
			),
			item_done(0, item),
			responses_done(),
		)

		chunks = list(parse_responses_stream([body]))
		self.assertEqual(joined(chunks, "thinking_delta"), "Step one.")

	def test_a_tool_call_is_assembled_from_its_fragments(self):
		opening = {
			"id": "fc_1",
			"type": "function_call",
			"call_id": "call_a",
			"name": "search_documents",
			"arguments": "",
			"status": "in_progress",
		}
		finished = dict(opening, arguments='{"doctype":"FA Test Ticket"}', status="completed")
		body = sse(
			item_added(0, opening),
			responses_frame("response.function_call_arguments.delta", output_index=0, delta='{"doctype"'),
			responses_frame(
				"response.function_call_arguments.delta", output_index=0, delta=':"FA Test Ticket"}'
			),
			responses_frame(
				"response.function_call_arguments.done",
				output_index=0,
				arguments='{"doctype":"FA Test Ticket"}',
			),
			item_done(0, finished),
			responses_done(tokens_in=12, tokens_out=9),
		)

		for label, stream in (("whole", [body]), ("byte by byte", one_byte_at_a_time(body))):
			with self.subTest(label):
				chunks = list(parse_responses_stream(stream))
				self.assertEqual(
					of_type(chunks, "toolcall_start")[0],
					{"type": "toolcall_start", "index": 0, "id": "call_a", "name": "search_documents"},
				)
				self.assertEqual(joined(chunks, "toolcall_delta"), '{"doctype":"FA Test Ticket"}')
				end = of_type(chunks, "toolcall_end")[0]
				# The call_id, never the item id: that is what a function_call_output
				# is paired on, and the two are different strings.
				self.assertEqual(end["id"], "call_a")
				self.assertEqual(end["name"], "search_documents")
				self.assertEqual(end["args"], {"doctype": "FA Test Ticket"})
				self.assertEqual(chunks[-1], {"type": "message_end", "reason": "toolUse"})

	def test_a_call_whose_arguments_never_streamed_is_still_whole(self):
		"""The finished item is the only copy an endpoint is obliged to send."""
		item = {
			"id": "fc_1",
			"type": "function_call",
			"call_id": "call_a",
			"name": "search_documents",
			"arguments": '{"doctype":"FA Test Ticket"}',
			"status": "completed",
		}
		body = sse(item_added(0, dict(item, arguments="")), item_done(0, item), responses_done())

		end = of_type(list(parse_responses_stream([body])), "toolcall_end")[0]
		self.assertEqual(end["args"], {"doctype": "FA Test Ticket"})

	def test_two_calls_keep_their_own_arguments(self):
		def call(index: int, name: str, args: str) -> tuple[str, str, str]:
			opening = {
				"id": f"fc_{index}",
				"type": "function_call",
				"call_id": f"call_{index}",
				"name": name,
				"arguments": "",
			}
			return (
				item_added(index, opening),
				responses_frame("response.function_call_arguments.delta", output_index=index, delta=args),
				item_done(index, dict(opening, arguments=args)),
			)

		first = call(0, "search_documents", '{"doctype":"FA Test Ticket"}')
		second = call(1, "read_document", '{"doctype":"FA Test Order"}')
		body = sse(*first, *second, responses_done())

		ends = of_type(list(parse_responses_stream([body])), "toolcall_end")
		self.assertEqual([end["id"] for end in ends], ["call_0", "call_1"])
		self.assertEqual([end["name"] for end in ends], ["search_documents", "read_document"])
		self.assertEqual(ends[0]["args"], {"doctype": "FA Test Ticket"})
		self.assertEqual(ends[1]["args"], {"doctype": "FA Test Order"})

	def test_truncation_is_reported_as_length(self):
		message = {"id": "msg_1", "type": "message", "role": "assistant", "content": []}
		body = sse(
			item_added(0, message),
			responses_frame("response.output_text.delta", output_index=0, delta="It was going to be"),
			responses_done(status="incomplete", incomplete_reason="max_output_tokens"),
		)

		chunks = list(parse_responses_stream([body]))
		# The block was still open when the answer ran out. It closes anyway.
		self.assertEqual(of_type(chunks, "text_end")[0]["text"], "It was going to be")
		self.assertEqual(chunks[-1], {"type": "message_end", "reason": "length"})

	def test_a_failed_response_ends_the_stream_in_plain_words(self):
		body = sse(
			responses_frame("response.created", response={"id": "resp_1", "status": "in_progress"}),
			responses_done(
				status="failed",
				error={"code": "server_error", "message": "The model could not be reached."},
			),
		)

		with self.assertRaises(ProviderError) as caught:
			list(parse_responses_stream([body]))
		self.assertIn("The model could not be reached.", str(caught.exception))

	def test_an_error_event_ends_the_stream(self):
		body = sse(responses_frame("error", code="rate_limit_exceeded", message="Slow down."))

		with self.assertRaises(ProviderError) as caught:
			list(parse_responses_stream([body]))
		self.assertIn("Slow down.", str(caught.exception))

	def test_an_enormous_error_message_is_cut_to_the_cap(self):
		body = sse(responses_frame("error", code="server_error", message="x" * (4 * ERROR_BODY_LIMIT)))

		with self.assertRaises(ProviderError) as caught:
			list(parse_responses_stream([body]))
		self.assertLessEqual(len(str(caught.exception)), ERROR_BODY_LIMIT + 200)

	def test_blocks_left_open_by_a_dropped_connection_are_closed(self):
		"""The socket dies mid-answer. Every block that opened still closes."""
		message = {"id": "msg_1", "type": "message", "role": "assistant", "content": []}
		body = sse(
			item_added(0, message),
			responses_frame("response.output_text.delta", output_index=0, delta="Half a sen"),
		)

		chunks = list(parse_responses_stream([body]))
		self.assertEqual(chunk_types(chunks)[-3:], ["text_delta", "text_end", "message_end"])
		self.assertEqual(of_type(chunks, "text_end")[0]["text"], "Half a sen")

	def test_frames_it_has_no_use_for_are_ignored(self):
		"""An event we do not read is not an event that breaks the run."""
		message = {"id": "msg_1", "type": "message", "role": "assistant", "content": []}
		body = sse(
			responses_frame("response.in_progress", response={"id": "resp_1", "status": "in_progress"}),
			responses_frame("response.queued", response={"id": "resp_1", "status": "queued"}),
			item_added(0, message),
			responses_frame("response.content_part.added", output_index=0, content_index=0),
			responses_frame("response.output_text.delta", output_index=0, delta="Hi"),
			responses_frame("response.output_text.annotation.added", output_index=0),
			responses_frame("response.content_part.done", output_index=0, content_index=0),
			item_done(0, message),
			responses_done(),
			": keep-alive",
		)

		chunks = list(parse_responses_stream([body]))
		self.assertEqual(joined(chunks, "text_delta"), "Hi")
		self.assertEqual(chunks[-1], {"type": "message_end", "reason": "stop"})

	def test_an_item_type_it_has_never_seen_does_not_derail_the_answer(self):
		message = {"id": "msg_1", "type": "message", "role": "assistant", "content": []}
		body = sse(
			item_added(0, {"id": "x_1", "type": "some_future_item"}),
			item_done(0, {"id": "x_1", "type": "some_future_item"}),
			item_added(1, message),
			responses_frame("response.output_text.delta", output_index=1, delta="Hi"),
			item_done(1, message),
			responses_done(),
		)

		chunks = list(parse_responses_stream([body]))
		self.assertEqual(joined(chunks, "text_delta"), "Hi")
		self.assertEqual(chunks[-1], {"type": "message_end", "reason": "stop"})

	def test_arguments_stop_at_the_cap(self):
		fragment = "x" * 32_000
		opening = {
			"id": "fc_1",
			"type": "function_call",
			"call_id": "call_a",
			"name": "search_documents",
			"arguments": "",
		}
		frames = [item_added(0, opening)]
		frames += [
			responses_frame("response.function_call_arguments.delta", output_index=0, delta=fragment)
			for _ in range(TOOL_ARGUMENTS_LIMIT // len(fragment) + 2)
		]

		with self.assertRaises(ProviderError) as caught:
			list(parse_responses_stream([sse(*frames)]))
		self.assertIn(str(TOOL_ARGUMENTS_LIMIT), str(caught.exception))
		self.assertIn("search_documents", str(caught.exception))

	def test_arguments_arriving_only_at_the_end_stop_at_the_cap_too(self):
		"""The settle path is the same buffer and gets the same ceiling."""
		opening = {
			"id": "fc_1",
			"type": "function_call",
			"call_id": "call_a",
			"name": "search_documents",
			"arguments": "",
		}
		body = sse(
			item_added(0, opening),
			item_done(0, dict(opening, arguments="x" * (TOOL_ARGUMENTS_LIMIT + 1))),
		)

		with self.assertRaises(ProviderError) as caught:
			list(parse_responses_stream([body]))
		self.assertIn(str(TOOL_ARGUMENTS_LIMIT), str(caught.exception))

	def test_a_line_that_never_ends_stops_at_the_cap(self):
		body = b"data: " + b"x" * (SSE_LINE_LIMIT + 1)

		with self.assertRaises(ProviderError) as caught:
			list(parse_responses_stream([body]))
		self.assertIn(str(SSE_LINE_LIMIT), str(caught.exception))


class TestStreamCeilings(AgentTestCase):
	"""The two things a stream may not do: grow a line forever, or an argument.

	Neither is a slow answer. A body that never ends a line and an endpoint that
	never stops sending argument fragments both fill the worker's memory until it
	dies, and a run that dies that way takes the queue with it. Both are refused
	in words that name the bound, and refusing closes the connection because the
	generator is what holds it open.
	"""

	def endless_line(self, pulled: list):
		"""A body that opens a data line and never ends it. Counts what was pulled."""
		chunk = b"data: " + b"x" * 64_000
		while True:
			pulled.append(len(chunk))
			yield chunk

	def test_a_line_that_never_ends_is_dropped_at_the_cap(self):
		for label, parser in (
			("openai", parse_openai_stream),
			("anthropic", parse_anthropic_stream),
			("responses", parse_responses_stream),
		):
			with self.subTest(label):
				pulled: list[int] = []
				with self.assertRaises(ProviderError) as caught:
					list(parser(self.endless_line(pulled)))

				self.assertIn(str(SSE_LINE_LIMIT), str(caught.exception))
				# Bounded, not merely finite: the stream stops within one chunk of
				# the cap rather than at whatever the body felt like sending.
				self.assertLess(sum(pulled), SSE_LINE_LIMIT + 128_000)

	def test_openai_arguments_stop_at_the_cap(self):
		fragment = "x" * 32_000
		frames = [
			openai_frame(
				{
					"tool_calls": [
						{
							"index": 0,
							"id": "call_a",
							"function": {"name": "search_documents", "arguments": fragment},
						}
					]
				}
			)
			for _ in range(TOOL_ARGUMENTS_LIMIT // len(fragment) + 2)
		]

		with self.assertRaises(ProviderError) as caught:
			list(parse_openai_stream([sse(*frames)]))

		self.assertIn(str(TOOL_ARGUMENTS_LIMIT), str(caught.exception))
		self.assertIn("search_documents", str(caught.exception))

	def test_anthropic_arguments_stop_at_the_cap(self):
		fragment = "x" * 32_000
		frames = [
			named_frame(
				"content_block_start",
				{
					"type": "content_block_start",
					"index": 0,
					"content_block": {
						"type": "tool_use",
						"id": "toolu_1",
						"name": "search_documents",
						"input": {},
					},
				},
			)
		]
		frames += [
			named_frame(
				"content_block_delta",
				{
					"type": "content_block_delta",
					"index": 0,
					"delta": {"type": "input_json_delta", "partial_json": fragment},
				},
			)
			for _ in range(TOOL_ARGUMENTS_LIMIT // len(fragment) + 2)
		]

		with self.assertRaises(ProviderError) as caught:
			list(parse_anthropic_stream([sse(*frames)]))

		self.assertIn(str(TOOL_ARGUMENTS_LIMIT), str(caught.exception))

	def test_a_call_just_under_the_cap_is_still_assembled(self):
		"""The cap is generous on purpose: a real argument object never nears it."""
		arguments = json.dumps({"doctype": "Task", "filters": "y" * 1000})
		body = sse(
			openai_frame(
				{
					"tool_calls": [
						{
							"index": 0,
							"id": "call_a",
							"function": {"name": "search_documents", "arguments": arguments},
						}
					]
				}
			),
			openai_frame({}, finish_reason="tool_calls"),
		)

		end = of_type(list(parse_openai_stream([body])), "toolcall_end")[0]
		self.assertEqual(end["args"]["doctype"], "Task")


class FakeStream:
	"""What `requests.post(stream=True)` hands back, minus the network.

	An error body arrives on the socket like every other body, so `text` is served
	through `iter_content` in chunk-sized pieces. Reading `.text` instead would be
	the whole body in memory, which is the thing the transport must not do.
	"""

	def __init__(self, chunks, status_code: int = 200, text: str = "", raises=None):
		self.chunks = chunks
		self.status_code = status_code
		self.text = text
		self.raises = raises
		self.closed = False
		self.pulled = 0

	def iter_content(self, chunk_size=None):
		if self.raises is not None:
			raise self.raises
		if self.status_code != 200 and self.text:
			body = self.text.encode()
			size = chunk_size or len(body)
			for start in range(0, len(body), size):
				chunk = body[start : start + size]
				self.pulled += len(chunk)
				yield chunk
			return
		yield from self.chunks

	def close(self):
		self.closed = True

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		self.close()
		return False


class TestStreamTransport(AgentTestCase):
	"""What goes on the wire, and what happens when nothing comes back."""

	def use_anthropic_provider(self) -> None:
		frappe.db.set_value("LLM Provider", PROVIDER, "provider_type", "Anthropic", update_modified=False)
		frappe.clear_document_cache("LLM Provider", PROVIDER)
		self.addCleanup(frappe.clear_document_cache, "LLM Provider", PROVIDER)

	def test_the_openai_request_asks_for_a_stream_and_for_usage(self):
		body = sse(openai_frame({"content": "Hi"}), openai_frame({}, finish_reason="stop"), "data: [DONE]")
		response = FakeStream([body])
		with patch("frappe_agents.runner.providers.requests.post", return_value=response) as post:
			chunks = list(call_model_stream(PROFILE, MESSAGES))

		payload = post.call_args.kwargs["json"]
		self.assertTrue(payload["stream"])
		self.assertEqual(payload["stream_options"], {"include_usage": True})
		self.assertEqual(payload["model"], "fa-test-model")
		self.assertEqual(joined(chunks, "text_delta"), "Hi")
		self.assertTrue(response.closed)

	def test_the_anthropic_request_asks_for_a_stream(self):
		self.use_anthropic_provider()
		body = sse(named_frame("message_stop", {"type": "message_stop"}))
		with patch("frappe_agents.runner.providers.requests.post", return_value=FakeStream([body])) as post:
			list(call_model_stream(PROFILE, [{"role": "system", "content": "S"}, *MESSAGES]))

		payload = post.call_args.kwargs["json"]
		self.assertTrue(payload["stream"])
		self.assertEqual(payload["system"], "S")
		self.assertNotIn("stream_options", payload)

	def test_the_timeout_is_a_connect_and_an_idle_budget_not_a_total(self):
		"""A streamed answer may take minutes. Only silence is a failure."""
		with patch("frappe_agents.runner.providers.requests.post", return_value=FakeStream([])) as post:
			list(call_model_stream(PROFILE, MESSAGES))
		self.assertEqual(post.call_args.kwargs["timeout"], (STREAM_CONNECT_TIMEOUT, STREAM_IDLE_TIMEOUT))
		self.assertTrue(post.call_args.kwargs["stream"])

	def test_silence_past_the_idle_budget_ends_the_stream_in_plain_words(self):
		response = FakeStream([], raises=requests.Timeout("read timed out"))
		with patch("frappe_agents.runner.providers.requests.post", return_value=response):
			with self.assertRaises(ProviderError) as caught:
				list(call_model_stream(PROFILE, MESSAGES))
		self.assertIn(str(STREAM_IDLE_TIMEOUT), str(caught.exception))
		self.assertTrue(response.closed)

	def test_a_refused_stream_never_shows_the_key(self):
		response = FakeStream([], status_code=401, text="bad key fa-test-key rejected")
		with patch("frappe_agents.runner.providers.requests.post", return_value=response):
			with self.assertRaises(ProviderError) as caught:
				list(call_model_stream(PROFILE, MESSAGES))
		message = str(caught.exception)
		self.assertIn("401", message)
		self.assertNotIn("fa-test-key", message)
		self.assertIn("***", message)
		self.assertTrue(response.closed)

	def test_an_enormous_error_body_is_capped_on_the_socket_not_afterwards(self):
		"""The endpoint decides how big its refusal is; we decide how much we read."""
		response = FakeStream([], status_code=500, text="x" * (4 * ERROR_BODY_BYTES))
		with patch("frappe_agents.runner.providers.requests.post", return_value=response):
			with self.assertRaises(ProviderError) as caught:
				list(call_model_stream(PROFILE, MESSAGES))

		self.assertLessEqual(response.pulled, ERROR_BODY_BYTES)
		self.assertLessEqual(len(str(caught.exception)), ERROR_BODY_LIMIT + 200)
		self.assertTrue(response.closed)

	def test_a_disabled_provider_is_refused_before_anything_is_pulled(self):
		"""The generator is not the place to find out the profile was never usable."""
		frappe.db.set_value("LLM Provider", PROVIDER, "enabled", 0, update_modified=False)
		frappe.clear_document_cache("LLM Provider", PROVIDER)
		self.addCleanup(frappe.clear_document_cache, "LLM Provider", PROVIDER)

		with patch("frappe_agents.runner.providers.requests.post") as post:
			with self.assertRaises(ProviderError):
				call_model_stream(PROFILE, MESSAGES)
		post.assert_not_called()

	def test_dropping_the_generator_closes_the_connection(self):
		"""The cancellation path: stop pulling and the socket goes away."""
		body = sse(openai_frame({"content": "Hi"}), openai_frame({"content": " there"}))
		response = FakeStream([body])
		with patch("frappe_agents.runner.providers.requests.post", return_value=response):
			stream = call_model_stream(PROFILE, MESSAGES)
			next(stream)
			stream.close()
		self.assertTrue(response.closed)


class TestProviderEndpoint(AgentTestCase):
	"""Where a provider may send prompts and the API key.

	A base URL is configuration, but it is also the outbound trust boundary: the
	host it names gets the prompt and the key. So the form refuses an unsafe one,
	the request refuses it again, and neither follows a redirect to somewhere the
	administrator never wrote down.
	"""

	def provider_doc(self, base_url: str, self_hosted: int = 0):
		doc = frappe.get_doc(
			{
				"doctype": "LLM Provider",
				"provider_name": f"FA Endpoint {frappe.generate_hash(length=8)}",
				"provider_type": "OpenAI Compatible",
				"base_url": base_url,
				"self_hosted": self_hosted,
				"api_key": "fa-endpoint-key",
				"enabled": 1,
			}
		)
		doc.flags.ignore_permissions = True
		return doc

	def test_provider_endpoint_validation_rejects_unsafe_destinations(self):
		refused = (
			"http://api.example.com/v1",
			"https://user:pass@api.example.com/v1",
			"https://10.1.2.3/v1",
			"https://172.16.4.5/v1",
			"https://192.168.0.9/v1",
			# The address a cloud instance's own credentials answer on.
			"https://169.254.169.254/latest",
			"https://127.0.0.1:8080/v1",
			"https://localhost:11434/v1",
			"ftp://api.example.com/v1",
			"api.example.com/v1",
		)
		for base_url in refused:
			with self.subTest(base_url=base_url):
				with self.assertRaises(frappe.ValidationError):
					self.provider_doc(base_url).insert(ignore_permissions=True)

		# https to a host somebody else runs needs no flag, and the same private
		# address is allowed once an administrator says they run it themselves.
		self.provider_doc("https://api.example.com/v1").insert(ignore_permissions=True)
		self.provider_doc("http://localhost:11434/v1", self_hosted=1).insert(ignore_permissions=True)

		# A redirect is refused rather than followed — on both wire paths, because
		# requests follows one on POST by default and would carry the key along.
		for call in (
			lambda: list(call_model_stream(PROFILE, MESSAGES)),
			lambda: call_model(PROFILE, MESSAGES),
		):
			moved = FakeStream([], status_code=302, text="Moved to https://elsewhere.example.com")
			with patch("frappe_agents.runner.providers.requests.post", return_value=moved) as post:
				with self.assertRaises(ProviderError) as caught:
					call()
			self.assertFalse(post.call_args.kwargs["allow_redirects"])
			self.assertIn("302", str(caught.exception))
			self.assertNotIn("fa-test-key", str(caught.exception))

		# And the request checks the destination itself: the fixture provider is
		# only usable because it says it is self-hosted. Take that away and nothing
		# goes out, with the key nowhere in what comes back.
		frappe.db.set_value("LLM Provider", PROVIDER, "self_hosted", 0, update_modified=False)
		frappe.clear_document_cache("LLM Provider", PROVIDER)
		self.addCleanup(frappe.clear_document_cache, "LLM Provider", PROVIDER)

		with patch("frappe_agents.runner.providers.requests.post") as post:
			with self.assertRaises(ProviderError) as caught:
				call_model_stream(PROFILE, MESSAGES)
		post.assert_not_called()
		message = str(caught.exception)
		self.assertIn(PROVIDER, message)
		self.assertIn("Edit the LLM Provider", message)
		self.assertNotIn("fa-test-key", message)
