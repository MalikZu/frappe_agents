# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""What the loop may and may not do with a tool.

The loop drives the conversation; it decides nothing about authority. Every call
goes through `execute_tool` and comes back as data — a denial is something the
model reads and answers around, never an exception the loop could swallow into a
retry. And every call leaves a row behind, including the one that was torn down
before it finished.
"""

import asyncio
from unittest.mock import patch

import frappe

from frappe_agents.runner.providers import ProviderError
from frappe_agents.tests.fixtures import (
	AGENT,
	DRAFT_USER,
	ORDER_DT,
	RESTRICTED_USER,
	TICKET_ALPHA,
	TICKET_DT,
	VAULT_DT,
	AgentTestCase,
	make_run,
	model_calls,
	model_says,
	run_events,
	run_with_model,
	tool_calls_for,
	tool_request,
)
from frappe_agents.tools.base import INTERRUPTED

ANSWER = model_says("I could not read that.", tokens_in=10, tokens_out=5)


def tool_results(events: list[dict]) -> list[dict]:
	return [event for event in events if event.get("type") == "tool_execution_end"]


def result_text(event: dict) -> str:
	return "".join(block.get("text") or "" for block in event["result"]["content"])


class TestRunLoop(AgentTestCase):
	def test_a_denied_tool_comes_back_as_a_result_and_the_run_goes_on(self):
		"""The autonomy ceiling: a refusal is data, not an exception.

		Raising it would end up in the loop's own error handler, which turns any
		exception into a tool result and keeps going — the same outcome by a path
		nobody audits. So the executor returns it, and the model is told.
		"""
		run = make_run(effective_user=RESTRICTED_USER)

		provider = run_with_model(
			run.name,
			[model_calls(tool_request("search_documents", {"doctype": VAULT_DT})), ANSWER],
		)

		self.assertEqual(provider.call_count, 2)
		self.assertEqual(
			frappe.db.get_value("Agent Run", run.name, "status"),
			"Completed",
		)

		calls = tool_calls_for(run.name)
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].outcome, "Denied")

		# The model was handed the refusal, and the event says it was one.
		result = provider.messages(1)[-1]
		self.assertIn('"ok": false', result.text)
		self.assertTrue(tool_results(run_events(run.name))[0]["isError"])

	def test_a_tool_that_fails_is_reported_as_an_error_result(self):
		run = make_run(effective_user=RESTRICTED_USER)

		run_with_model(
			run.name,
			[
				model_calls(
					tool_request("search_documents", {"doctype": TICKET_DT, "fields": ["no_such_field"]})
				),
				ANSWER,
			],
		)

		calls = tool_calls_for(run.name)
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].outcome, "Error")
		self.assertTrue(tool_results(run_events(run.name))[0]["isError"])

	def test_a_tool_the_agent_was_never_given_is_refused_and_audited(self):
		"""The loop answers "tool not found" by itself, which would leave no row.

		So an unknown name is sent through the executor like every other call and
		refused there, where the audit lives.
		"""
		run = make_run(effective_user=RESTRICTED_USER)

		run_with_model(run.name, [model_calls(tool_request("delete_everything", {})), ANSWER])

		calls = tool_calls_for(run.name)
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].tool, "delete_everything")
		self.assertEqual(calls[0].outcome, "Denied")
		self.assertTrue(tool_results(run_events(run.name))[0]["isError"])

	def test_a_successful_tool_is_not_marked_an_error(self):
		"""The control for the three above: success comes back as success."""
		run = make_run(effective_user=RESTRICTED_USER)

		run_with_model(
			run.name,
			[
				model_calls(tool_request("search_documents", {"doctype": TICKET_DT, "fields": ["name"]})),
				model_says("One ticket."),
			],
		)

		events = tool_results(run_events(run.name))
		self.assertFalse(events[0]["isError"])
		self.assertIn(TICKET_ALPHA, result_text(events[0]))

	def test_two_tool_calls_in_one_turn_both_run_and_both_are_audited(self):
		run = make_run(effective_user=RESTRICTED_USER)

		run_with_model(
			run.name,
			[
				model_calls(
					tool_request("search_documents", {"doctype": TICKET_DT}, call_id="call_1"),
					tool_request("get_doctype_meta", {"doctype": TICKET_DT}, call_id="call_2"),
				),
				model_says("Both done."),
			],
		)

		calls = tool_calls_for(run.name)
		self.assertEqual([call.tool for call in calls], ["search_documents", "get_doctype_meta"])
		self.assertEqual({call.outcome for call in calls}, {"Success"})

	def test_a_call_torn_down_mid_flight_is_still_audited(self):
		"""The one path `execute_tool` cannot write its own row on.

		A cancelled task raises through the executor before the tool returns, and
		the loop re-raises it without ever reaching the hook that would record the
		call. The row is written by the shim on its way out instead, so an
		auditor sees an attempt rather than a gap. Nothing but a worker teardown
		produces this, so the teardown is what the test simulates.
		"""
		run = make_run(effective_user=RESTRICTED_USER)

		with patch("frappe_agents.runner.run.execute_tool", side_effect=asyncio.CancelledError):
			with self.assertRaises(asyncio.CancelledError):
				run_with_model(
					run.name, [model_calls(tool_request("search_documents", {"doctype": TICKET_DT}))]
				)

		calls = tool_calls_for(run.name)
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].tool, "search_documents")
		self.assertEqual(calls[0].error, INTERRUPTED)

	def test_a_tool_that_finished_is_not_also_audited_as_interrupted(self):
		"""Something failing after the tool must not rewrite what the tool did.

		Publishing the result to the browser happens once the tool has already
		run and already written its row. If that push throws, the call is still
		a call that finished: one row, Success, and no second row claiming it
		never got there.
		"""
		run = make_run(effective_user=RESTRICTED_USER)

		def refuse_to_publish(run_doc, event_type, **payload):
			if event_type == "tool_call":
				raise RuntimeError("the browser is unreachable")

		with patch("frappe_agents.runner.run.publish_event", side_effect=refuse_to_publish):
			run_with_model(
				run.name,
				[model_calls(tool_request("search_documents", {"doctype": TICKET_DT})), ANSWER],
			)

		calls = tool_calls_for(run.name)
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].outcome, "Success")
		self.assertNotEqual(calls[0].error, INTERRUPTED)

	def test_a_provider_that_raises_fails_the_run_in_its_own_words(self):
		run = make_run(effective_user=RESTRICTED_USER)

		run_with_model(run.name, [ProviderError("Model request to http://localhost:1 failed.")])

		values = frappe.db.get_value("Agent Run", run.name, ["status", "error"], as_dict=True)
		self.assertEqual(values.status, "Failed")
		self.assertIn("http://localhost:1", values.error)

	def test_the_model_is_offered_only_the_tools_the_agent_holds(self):
		run = make_run(effective_user=RESTRICTED_USER, agent=AGENT)

		provider = run_with_model(run.name, [model_says("Nothing to do.")])

		offered = provider.tool_names()
		self.assertIn("search_documents", offered)
		self.assertNotIn("delete_everything", offered)

	def test_the_autonomy_ceiling_holds_when_the_model_asks_for_a_write(self):
		"""A Suggest agent holds create_draft and still may not call it.

		The gate is in the executor, so it does not matter who asks or how. The
		model asking directly is the case the loop could have made a way around.
		"""
		run = make_run(effective_user=DRAFT_USER, agent=AGENT)
		before = frappe.db.count(ORDER_DT)

		run_with_model(
			run.name,
			[
				model_calls(
					tool_request(
						"create_draft",
						{"doctype": ORDER_DT, "values": {"order_title": "FA Loop Draft"}},
					)
				),
				ANSWER,
			],
		)

		calls = tool_calls_for(run.name)
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].outcome, "Denied")
		self.assertEqual(frappe.db.count(ORDER_DT), before)
