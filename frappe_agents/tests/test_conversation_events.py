# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""What a reopened conversation gets back.

A proposal card is published while the run is going, so a reload has nothing to
draw it from except the run's event log. `get_conversation` hands that log back
parsed, and a log it cannot read must cost the caller nothing more than the log.
"""

from unittest.mock import patch

import frappe

from frappe_agents.api import get_conversation, start_run
from frappe_agents.tests.fixtures import AGENT, RESTRICTED_USER, AgentTestCase, as_user

EVENTS = [
	{"type": "agent_start"},
	{
		"type": "tool_execution_start",
		"toolCallId": "call_1",
		"toolName": "propose_submit",
		"args": {"doctype": "FA Test Order", "name": "FA-1", "reason": "Checked the prices."},
	},
]


class TestConversationEvents(AgentTestCase):
	def start(self) -> dict:
		with as_user(RESTRICTED_USER), patch("frappe.enqueue"):
			return start_run(agent=AGENT, message="Please propose it")

	def set_log(self, run: str, log: str | None) -> None:
		frappe.db.set_value("Agent Run", run, "event_log", log, update_modified=False)

	def read(self, conversation: str) -> dict:
		with as_user(RESTRICTED_USER):
			return get_conversation(conversation)

	def test_a_runs_events_come_back_parsed(self):
		started = self.start()
		self.set_log(started["run"], frappe.as_json({"events": EVENTS, "truncated": False}))

		runs = self.read(started["conversation"])["runs"]
		self.assertEqual(len(runs), 1)
		self.assertEqual(runs[0]["name"], started["run"])
		self.assertEqual(runs[0]["event_log"], EVENTS)

	def test_a_run_with_no_events_comes_back_empty(self):
		started = self.start()

		runs = self.read(started["conversation"])["runs"]
		self.assertEqual(runs[0]["event_log"], [])

	def test_an_unreadable_log_costs_only_the_log(self):
		started = self.start()
		self.set_log(started["run"], "{not json at all")

		runs = self.read(started["conversation"])["runs"]
		self.assertEqual(runs[0]["event_log"], [])
		self.assertEqual(runs[0]["input_message"], "Please propose it")

	def test_only_events_that_are_objects_survive(self):
		started = self.start()
		self.set_log(
			started["run"], frappe.as_json({"events": [EVENTS[0], "turn_start", None], "truncated": True})
		)

		runs = self.read(started["conversation"])["runs"]
		self.assertEqual(runs[0]["event_log"], [EVENTS[0]])
