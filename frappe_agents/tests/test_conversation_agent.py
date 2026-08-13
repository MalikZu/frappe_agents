# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Switching agents mid-conversation.

A conversation belongs to one agent. Every run in it is audited against that
agent's grants — its tools, its autonomy, its allowed roles — so a transcript
that changed agents halfway would be a transcript no single grant explains.

The chat surface answers a switch by starting a new conversation. These tests
say what that costs the old one, which is nothing: it keeps its agent, its runs
and its last activity, and it is still in the rail.
"""

from unittest.mock import patch

import frappe

from frappe_agents.api import get_conversation, list_conversations, start_run
from frappe_agents.tests.fixtures import (
	AGENT,
	DRAFT_AGENT,
	RESTRICTED_USER,
	AgentTestCase,
	as_user,
)


class TestConversationAgent(AgentTestCase):
	def start(self, agent: str = AGENT, message: str = "Who is on this?", **values) -> dict:
		with as_user(RESTRICTED_USER), patch("frappe.enqueue"):
			return start_run(agent=agent, message=message, **values)

	def test_switching_agents_starts_a_new_conversation(self):
		first = self.start(AGENT, "Ask the reader")
		second = self.start(DRAFT_AGENT, "Ask the drafter")

		self.assertNotEqual(second["conversation"], first["conversation"])
		self.assertEqual(
			frappe.db.get_value("Agent Conversation", second["conversation"], "agent"), DRAFT_AGENT
		)

	def test_the_old_conversation_is_untouched_by_the_switch(self):
		first = self.start(AGENT, "Ask the reader")
		before = frappe.db.get_value(
			"Agent Conversation", first["conversation"], ["agent", "last_activity"], as_dict=True
		)

		self.start(DRAFT_AGENT, "Ask the drafter")

		after = frappe.db.get_value(
			"Agent Conversation", first["conversation"], ["agent", "last_activity"], as_dict=True
		)
		self.assertEqual(after.agent, AGENT)
		self.assertEqual(after.last_activity, before.last_activity)

		with as_user(RESTRICTED_USER):
			payload = get_conversation(first["conversation"])
		self.assertEqual([run["name"] for run in payload["runs"]], [first["run"]])

	def test_both_conversations_stay_in_the_rail(self):
		first = self.start(AGENT, "Ask the reader")
		second = self.start(DRAFT_AGENT, "Ask the drafter")

		with as_user(RESTRICTED_USER):
			rows = {row["name"]: row for row in list_conversations()}

		self.assertEqual(rows[first["conversation"]]["agent"], AGENT)
		self.assertEqual(rows[second["conversation"]]["agent"], DRAFT_AGENT)

	def test_another_agents_conversation_cannot_be_continued(self):
		"""The wall behind the new conversation: continuing is refused, not reassigned."""
		first = self.start(AGENT, "Ask the reader")

		with as_user(RESTRICTED_USER), patch("frappe.enqueue") as enqueue:
			with self.assertRaises(frappe.ValidationError):
				start_run(
					agent=DRAFT_AGENT,
					message="Now you take over",
					conversation=first["conversation"],
				)
			enqueue.assert_not_called()

		self.assertEqual(frappe.db.get_value("Agent Conversation", first["conversation"], "agent"), AGENT)
		self.assertEqual(frappe.db.count("Agent Run", {"conversation": first["conversation"]}), 1)
