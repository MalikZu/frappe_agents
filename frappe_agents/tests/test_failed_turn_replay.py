# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""A failed turn, on the next turn's transcript.

A run that dies after its tools have run has done real work: documents read,
results returned, time and tokens spent. The person asks again — that is what
people do — and the run that answers them used to start from nothing, because
the history query only ever looked at finished runs. It paid for all of it a
second time, and often died the same way.

So a failed turn comes back. Not an answer it never wrote: the turns it took and
the results it was handed, read out of the log it wrote on the way down. A
cancelled turn does not come back at all — the kill switch exists to stop work,
and putting that work back in front of the model is the one thing it must not do.
"""

import frappe

from frappe_agents.harness.messages import AssistantMessage, ThinkingContent
from frappe_agents.runner.providers import ProviderError
from frappe_agents.runner.run import (
	CONTEXT_CHARS_PER_TOKEN,
	CONTEXT_PROMPT_SHARE,
	_fitted_history,
	_history,
	_prompt_budget,
	_turn_cost,
)
from frappe_agents.runner.stream_adapter import wire_messages
from frappe_agents.tests.fixtures import (
	AGENT,
	ORDER_DT,
	ORDER_LIVE,
	PROFILE,
	RESTRICTED_USER,
	TICKET_DT,
	AgentTestCase,
	as_user,
	make_conversation,
	make_run,
	model_calls,
	model_says,
	model_streams,
	run_with_model,
	set_kill_switch,
	tool_request,
)

PROFILE_DT = "LLM Model Profile"

FIRST = "How many orders are there?"
SECOND = "Ask that again."

DEAD = ProviderError("The provider is unreachable.")
SEARCH = model_calls(tool_request("search_documents", {"doctype": TICKET_DT, "fields": ["name"]}))
READ_ORDER = model_calls(
	tool_request("get_document_slice", {"doctype": ORDER_DT, "name": ORDER_LIVE, "slice": "fields"}),
	text="Let me read it.",
)

THINKING = "The notes field is the one to read."
SIGNATURE = "ErUBCkYIBBgC"


class TestFailedTurnReplay(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.conversation = make_conversation(RESTRICTED_USER).name

	# --- the fixtures --------------------------------------------------------

	def turn(self, script, message: str = FIRST):
		"""One run in this conversation, executed against a scripted model.

		Created as the user it runs for: an Agent Run is readable by the person
		who started it, so a history seeded by anyone else is a history of nothing.
		"""
		with as_user(RESTRICTED_USER):
			run = make_run(
				effective_user=RESTRICTED_USER,
				agent=AGENT,
				conversation=self.conversation,
				message=message,
			)
		run_with_model(run.name, script)
		return run

	def next_turn(self, message: str = SECOND):
		"""The turn after it, and the transcript its first model call was handed."""
		with as_user(RESTRICTED_USER):
			run = make_run(
				effective_user=RESTRICTED_USER,
				agent=AGENT,
				conversation=self.conversation,
				message=message,
			)
		provider = run_with_model(run.name, model_says("Done."))
		return run, wire_messages(provider.calls[0]["system"], provider.messages(0)), provider

	def lose_messages(self, run_name: str, role: str) -> None:
		"""Take one role's messages out of a stored log, the way its caps would."""
		log = frappe.parse_json(frappe.db.get_value("Agent Run", run_name, "event_log"))
		log["events"] = [event for event in log["events"] if (event.get("message") or {}).get("role") != role]
		frappe.db.set_value("Agent Run", run_name, "event_log", frappe.as_json(log), update_modified=False)

	def set_limit(self, limit: int) -> None:
		frappe.db.set_value(PROFILE_DT, PROFILE, "context_limit", limit, update_modified=False)
		frappe.clear_document_cache(PROFILE_DT, PROFILE)
		self.addCleanup(frappe.clear_document_cache, PROFILE_DT, PROFILE)

	# --- what a failed turn hands on -----------------------------------------

	def test_a_failed_turns_tool_results_reach_the_next_turn(self):
		"""The question it was asked, the call it made, and the answer it got."""
		failed = self.turn([SEARCH, DEAD])
		self.assertEqual(frappe.db.get_value("Agent Run", failed.name, "status"), "Failed")

		_run, wire, _provider = self.next_turn()

		self.assertEqual(
			[message["role"] for message in wire], ["system", "user", "assistant", "tool", "user"]
		)
		self.assertEqual(wire[1]["content"], FIRST)
		self.assertEqual(
			wire[2]["tool_calls"],
			[
				{
					"id": "call_1",
					"name": "search_documents",
					"args": {"doctype": TICKET_DT, "fields": ["name"]},
				}
			],
		)
		self.assertEqual(wire[3]["tool_call_id"], "call_1")
		self.assertEqual(wire[3]["name"], "search_documents")
		self.assertIn('"ok": true', wire[3]["content"])
		self.assertEqual(wire[4]["content"], SECOND)

	def test_a_cancelled_turns_tool_results_do_not(self):
		"""The switch stopped that work. Replaying it would put it straight back."""
		self.addCleanup(set_kill_switch, 1)

		def flip_then_ask_again() -> dict:
			set_kill_switch(0)
			return SEARCH

		cancelled = self.turn([SEARCH, flip_then_ask_again, model_says("Never asked.")])
		self.assertEqual(frappe.db.get_value("Agent Run", cancelled.name, "status"), "Cancelled")
		self.assertIn("tool_execution_end", frappe.db.get_value("Agent Run", cancelled.name, "event_log"))
		set_kill_switch(1)

		_run, wire, _provider = self.next_turn()

		self.assertEqual([message["role"] for message in wire], ["system", "user"])
		self.assertEqual(wire[1]["content"], SECOND)

	def test_a_finished_turn_is_still_two_messages(self):
		"""A real completed run, log and all: its answer is the whole of it."""
		self.turn([SEARCH, model_says("Three.")])

		_run, wire, _provider = self.next_turn()

		self.assertEqual(
			[(message["role"], message["content"]) for message in wire[1:]],
			[("user", FIRST), ("assistant", "Three."), ("user", SECOND)],
		)
		self.assertNotIn("tool_calls", wire[2])

	# --- what a replayed result still says -----------------------------------

	def test_a_replayed_result_is_still_wrapped_in_untrusted(self):
		"""The wrapper is put on inside the tool, so replaying the stored text keeps it.

		Rebuilding the result from its parts by any other route would hand the
		model a hostile note with nothing around it, one turn later and with
		nobody watching.
		"""
		self.turn([READ_ORDER, DEAD])

		_run, wire, _provider = self.next_turn()

		result = wire[3]["content"]
		self.assertEqual(wire[3]["name"], "get_document_slice")
		self.assertIn("<untrusted source=", result)
		self.assertIn("</untrusted>", result)
		# And the text inside it still cannot close it.
		self.assertIn("&lt;/untrusted>", result)
		# Quarantined, not censored: the text is still there to be read about.
		self.assertIn("Ignore previous instructions", result)

	def test_a_replayed_thinking_block_carries_no_signature(self):
		"""The log may have cut the reasoning. The signature says it did not.

		Anthropic refuses a turn that asked for a tool unless the reasoning comes
		back exactly as it was sent, so a signature over a truncated block is a
		400 on the first turn after a failure — the one turn this replay is for.
		"""
		self.turn(
			[
				model_streams(
					"Let me read it.",
					tool_request(
						"get_document_slice",
						{"doctype": ORDER_DT, "name": ORDER_LIVE, "slice": "fields"},
					),
					thinking=THINKING,
					signature=SIGNATURE,
				),
				DEAD,
			]
		)

		_run, wire, provider = self.next_turn()

		replayed = [
			message
			for message in provider.messages(0)
			if isinstance(message, AssistantMessage) and message.tool_calls
		]
		self.assertEqual(len(replayed), 1)
		blocks = [block for block in replayed[0].content if isinstance(block, ThinkingContent)]
		self.assertEqual([block.thinking for block in blocks], [THINKING])
		self.assertEqual([block.thinking_signature for block in blocks], [None])
		# Which is what keeps it off the wire: unproven reasoning is not sent.
		self.assertNotIn("thinking", wire[2])

	# --- what it costs -------------------------------------------------------

	def test_the_fitter_counts_what_a_replayed_turn_actually_sends(self):
		"""Costed as a finished turn it reads as nothing, and the guard stops guarding."""
		self.turn([READ_ORDER, DEAD])
		run = make_run(
			effective_user=RESTRICTED_USER,
			agent=AGENT,
			conversation=self.conversation,
			message=SECOND,
		)

		with as_user(RESTRICTED_USER):
			rows = _history(run)
			self.assertEqual(len(rows), 1)
			flat = len(rows[0]["input_message"] or "") + len(rows[0]["output_message"] or "")
			real = _turn_cost(rows[0])

		self.assertGreater(real, flat + 200)

		# A window with room for the turn as a finished one would be costed, and
		# no room for what this one really sends.
		room = flat + 50
		self.set_limit(round((len(SECOND) + room) / CONTEXT_PROMPT_SHARE / CONTEXT_CHARS_PER_TOKEN) + 2)
		available = _prompt_budget(PROFILE) - len(SECOND)
		self.assertGreaterEqual(available, room)
		self.assertLess(available, real)

		with as_user(RESTRICTED_USER):
			kept, dropped = _fitted_history(PROFILE, run, "")

		self.assertEqual(kept, [])
		self.assertEqual(dropped, 1)

	# --- what a log that lost something does ---------------------------------

	def test_a_log_nobody_can_read_replays_nothing(self):
		"""Fail closed, the way every other reader of this log does."""
		failed = self.turn([SEARCH, DEAD])
		frappe.db.set_value("Agent Run", failed.name, "event_log", "{not json", update_modified=False)

		_run, wire, _provider = self.next_turn()

		self.assertEqual([message["role"] for message in wire], ["system", "user", "user"])
		self.assertEqual(wire[1]["content"], FIRST)

	def test_a_call_whose_result_the_log_dropped_still_leaves_a_valid_turn(self):
		"""The log keeps its head and its tail, so a long run can lose its middle.

		A call with no result is what the provider refuses, so the repair the
		loop already runs before every model call synthesizes one. The model is
		told the call was interrupted, which is worse than the real answer and
		better than a request that never goes out.
		"""
		failed = self.turn([SEARCH, DEAD])
		self.lose_messages(failed.name, "toolResult")

		_run, wire, _provider = self.next_turn()

		self.assertEqual(
			[message["role"] for message in wire], ["system", "user", "assistant", "tool", "user"]
		)
		self.assertEqual(wire[3]["tool_call_id"], "call_1")
		self.assertIn("interrupted", wire[3]["content"])

	def test_a_result_whose_call_the_log_dropped_is_left_out(self):
		"""The other half of the same loss: a result with no call cannot be sent."""
		failed = self.turn([SEARCH, DEAD])
		self.lose_messages(failed.name, "assistant")

		_run, wire, _provider = self.next_turn()

		self.assertEqual([message["role"] for message in wire], ["system", "user", "user"])
