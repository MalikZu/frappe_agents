# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The kill switch.

Clearing Agent Settings.global_enabled has to stop everything: no new run may be
started, a run already on the queue must not proceed, and a run in flight must
abort at its next tool call rather than finish the job.
"""

from unittest.mock import patch

import frappe

from frappe_agents.api import start_run
from frappe_agents.runner.run import execute_run
from frappe_agents.tests.fixtures import (
	AGENT,
	RESTRICTED_USER,
	TICKET_DT,
	AgentTestCase,
	as_user,
	make_run,
	set_kill_switch,
	tool_calls_for,
)
from frappe_agents.tools.base import KillSwitchActive, execute_tool


class TestKillSwitch(AgentTestCase):
	def disable_runtime(self) -> None:
		set_kill_switch(0)
		self.addCleanup(set_kill_switch, 1)

	def test_start_run_throws_when_the_runtime_is_off(self):
		self.disable_runtime()
		before = frappe.db.count("Agent Run")

		with as_user(RESTRICTED_USER):
			with self.assertRaises(frappe.ValidationError):
				start_run(agent=AGENT, message="Anything at all")

		self.assertEqual(frappe.db.count("Agent Run"), before)

	def test_queued_run_is_cancelled_when_the_runtime_is_off(self):
		run = make_run(effective_user=RESTRICTED_USER)
		self.disable_runtime()

		with patch("frappe_agents.runner.run.call_model") as call_model:
			execute_run(run.name)
			call_model.assert_not_called()

		values = frappe.db.get_value("Agent Run", run.name, ["status", "error"], as_dict=True)
		self.assertEqual(values.status, "Cancelled")
		self.assertTrue(values.error)
		self.assertEqual(frappe.db.count("Agent Tool Call", {"run": run.name}), 0)

	def test_run_aborts_at_the_next_tool_call(self):
		"""Switch thrown mid-run: the tool is refused and the run stops there."""
		run = make_run(effective_user=RESTRICTED_USER)
		self.addCleanup(set_kill_switch, 1)

		def flip_then_ask_for_a_tool(*args, **kwargs):
			set_kill_switch(0)
			return {
				"text": None,
				"tool_calls": [{"id": "call_1", "name": "search_documents", "args": {"doctype": TICKET_DT}}],
				"tokens_in": 10,
				"tokens_out": 5,
			}

		with patch("frappe_agents.runner.run.call_model", side_effect=flip_then_ask_for_a_tool) as call_model:
			execute_run(run.name)

		self.assertEqual(call_model.call_count, 1)

		values = frappe.db.get_value("Agent Run", run.name, ["status", "output_message"], as_dict=True)
		self.assertEqual(values.status, "Cancelled")
		self.assertFalse(values.output_message)

		# The refusal is still audited: an auditor can see what was attempted.
		calls = tool_calls_for(run.name)
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].outcome, "Denied")
		self.assertEqual(calls[0].tool, "search_documents")

	def test_tool_call_is_refused_directly_when_the_runtime_is_off(self):
		run = make_run(effective_user=RESTRICTED_USER)
		self.disable_runtime()

		with as_user(RESTRICTED_USER):
			with self.assertRaises(KillSwitchActive):
				execute_tool(run, "search_documents", {"doctype": TICKET_DT})

		calls = tool_calls_for(run.name)
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].outcome, "Denied")
