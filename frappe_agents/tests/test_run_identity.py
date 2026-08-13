# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The identity assertion, and the loop that depends on it.

A run carries the user it must execute as. If that user is Administrator, Guest,
or disabled, the run fails before a single model call — the whole permission
model rests on this being true.

No model is ever reached: the runner's provider is a scripted fake, so the loop,
the tools and the audit rows are the real ones and only the answers are canned.
"""

import frappe

from frappe_agents.tests.fixtures import (
	AGENT,
	DISABLED_USER,
	RESTRICTED_USER,
	TICKET_ALPHA,
	TICKET_BETA,
	TICKET_DT,
	AgentTestCase,
	make_conversation,
	make_run,
	model_calls,
	model_says,
	run_with_model,
	tool_calls_for,
	tool_request,
)

SEARCH_TICKETS = model_calls(
	tool_request("search_documents", {"doctype": TICKET_DT, "fields": ["name", "subject"], "limit": 10}),
	tokens_in=120,
	tokens_out=30,
)
ANSWER = model_says("You have one ticket: FA Ticket Alpha.", tokens_in=200, tokens_out=25)


def run_values(run_name: str) -> dict:
	return frappe.db.get_value(
		"Agent Run",
		run_name,
		["status", "error", "output_message", "steps_taken", "tokens_in", "tokens_out"],
		as_dict=True,
	)


class TestRunIdentity(AgentTestCase):
	def test_run_as_administrator_is_refused(self):
		run = make_run(effective_user="Administrator")

		provider = run_with_model(run.name)

		self.assertEqual(provider.call_count, 0)
		values = run_values(run.name)
		self.assertEqual(values.status, "Failed")
		self.assertIn("Administrator", values.error)
		self.assertEqual(frappe.db.count("Agent Tool Call", {"run": run.name}), 0)

	def test_run_as_disabled_user_is_refused(self):
		run = make_run(effective_user=DISABLED_USER)

		provider = run_with_model(run.name)

		self.assertEqual(provider.call_count, 0)
		values = run_values(run.name)
		self.assertEqual(values.status, "Failed")
		self.assertIn(DISABLED_USER, values.error)
		self.assertEqual(frappe.db.count("Agent Tool Call", {"run": run.name}), 0)

	def test_run_as_disabled_agent_is_refused(self):
		frappe.db.set_value("Agent", AGENT, "enabled", 0)
		frappe.clear_document_cache("Agent", AGENT)
		self.addCleanup(frappe.clear_document_cache, "Agent", AGENT)
		self.addCleanup(frappe.db.set_value, "Agent", AGENT, "enabled", 1)

		run = make_run(effective_user=RESTRICTED_USER)
		provider = run_with_model(run.name)

		self.assertEqual(provider.call_count, 0)
		self.assertEqual(run_values(run.name).status, "Failed")

	def test_run_executes_tools_as_the_effective_user(self):
		"""The full loop: one tool call under the effective user, then an answer."""
		conversation = make_conversation(RESTRICTED_USER)
		run = make_run(effective_user=RESTRICTED_USER, conversation=conversation.name)

		provider = run_with_model(run.name, [SEARCH_TICKETS, ANSWER])

		self.assertEqual(provider.call_count, 2)
		self.assertIn("search_documents", provider.tool_names())

		values = run_values(run.name)
		self.assertEqual(values.status, "Completed")
		self.assertEqual(values.output_message, ANSWER["text"])
		self.assertEqual(values.steps_taken, 2)
		self.assertEqual(values.tokens_in, 320)
		self.assertEqual(values.tokens_out, 55)

		calls = tool_calls_for(run.name)
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].outcome, "Success")
		# The run read the ticket its user is permitted to see, and only that one.
		self.assertIn(TICKET_ALPHA, calls[0].docs_touched)
		self.assertNotIn(TICKET_BETA, calls[0].docs_touched)

		# The job must hand the session back exactly as it found it.
		self.assertEqual(frappe.session.user, "Administrator")

	def test_the_tool_result_goes_back_to_the_model(self):
		"""The second call carries the first call's result, or the model is blind."""
		run = make_run(effective_user=RESTRICTED_USER)

		provider = run_with_model(run.name, [SEARCH_TICKETS, ANSWER])

		second = provider.messages(1)
		self.assertEqual(second[-1].tool_call_id, "call_1")
		self.assertEqual(second[-1].tool_name, "search_documents")
		self.assertIn(TICKET_ALPHA, second[-1].text)

	def test_run_stops_at_max_steps(self):
		"""A model that only ever asks for tools does not loop forever."""
		frappe.db.set_value("Agent", AGENT, "max_steps", 2)
		frappe.clear_document_cache("Agent", AGENT)
		self.addCleanup(frappe.clear_document_cache, "Agent", AGENT)
		self.addCleanup(frappe.db.set_value, "Agent", AGENT, "max_steps", 5)

		run = make_run(effective_user=RESTRICTED_USER)
		provider = run_with_model(run.name, [SEARCH_TICKETS])

		self.assertEqual(provider.call_count, 2)
		values = run_values(run.name)
		self.assertEqual(values.status, "Failed")
		self.assertEqual(values.steps_taken, 2)
		self.assertIn("2 steps", values.error)
		self.assertEqual(len(tool_calls_for(run.name)), 2)
