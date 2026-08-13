# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The conversation rail.

Two rules and nothing else. You see your own conversations and no one else's —
the doctype grants read to the owner alone and the query filters on the user as
well, so both would have to fail together. And a rail row carries no part of what
a run produced: a snippet is the title or the first thing the person typed, never
a tool call, a tool result or an answer.
"""

from unittest.mock import patch

import frappe

from frappe_agents.api import list_conversations, start_run
from frappe_agents.tests.fixtures import (
	AGENT,
	AUDITOR_ROLE,
	OPEN_USER,
	RESTRICTED_USER,
	AgentTestCase,
	as_user,
	make_conversation,
	make_run,
)


class TestConversationList(AgentTestCase):
	def start(self, user: str, message: str, **values) -> dict:
		with as_user(user), patch("frappe.enqueue"):
			return start_run(agent=AGENT, message=message, **values)

	def listed(self, user: str) -> list[dict]:
		with as_user(user):
			return list_conversations()

	def test_a_user_sees_their_own_conversations_and_no_others(self):
		mine = self.start(RESTRICTED_USER, "What is on my plate?")
		theirs = self.start(OPEN_USER, "What is on their plate?")

		names = {row["name"] for row in self.listed(RESTRICTED_USER)}
		self.assertIn(mine["conversation"], names)
		self.assertNotIn(theirs["conversation"], names)

		names = {row["name"] for row in self.listed(OPEN_USER)}
		self.assertIn(theirs["conversation"], names)
		self.assertNotIn(mine["conversation"], names)

	def test_a_row_carries_the_agent_name_and_a_snippet(self):
		started = self.start(RESTRICTED_USER, "How many tickets are open?")

		row = self.row(RESTRICTED_USER, started["conversation"])
		self.assertEqual(row["agent"], AGENT)
		self.assertEqual(row["agent_name"], AGENT)
		self.assertEqual(row["snippet"], "How many tickets are open?")
		self.assertTrue(row["last_activity"])

	def test_a_conversation_without_a_title_falls_back_to_its_first_message(self):
		conversation = make_conversation(RESTRICTED_USER, title="")
		make_run(effective_user=RESTRICTED_USER, conversation=conversation.name, message="The first thing")
		make_run(effective_user=RESTRICTED_USER, conversation=conversation.name, message="The second thing")

		row = self.row(RESTRICTED_USER, conversation.name)
		self.assertEqual(row["snippet"], "The first thing")

	def test_the_rail_carries_nothing_a_run_produced(self):
		started = self.start(RESTRICTED_USER, "Please propose it")
		frappe.db.set_value(
			"Agent Run",
			started["run"],
			{
				"event_log": frappe.as_json({"events": [{"type": "tool_execution_start"}]}),
				"output_message": "I called four tools and here is what they said.",
			},
			update_modified=False,
		)

		row = self.row(RESTRICTED_USER, started["conversation"])
		self.assertNotIn("event_log", row)
		self.assertNotIn("runs", row)
		blob = frappe.as_json(row)
		self.assertNotIn("tool_execution_start", blob)
		self.assertNotIn("four tools", blob)

	def test_newest_activity_comes_first(self):
		older = self.start(RESTRICTED_USER, "The older question")
		newer = self.start(RESTRICTED_USER, "The newer question")
		frappe.db.set_value(
			"Agent Conversation", older["conversation"], "last_activity", "2020-01-01 00:00:00"
		)

		names = [row["name"] for row in self.listed(RESTRICTED_USER)]
		self.assertLess(names.index(newer["conversation"]), names.index(older["conversation"]))

	def test_the_rail_needs_the_agent_user_role(self):
		"""A throwaway user rather than one of the cast: the cast all hold the role,
		and taking it off one of them would outlive the rollback in the role cache."""
		outsider = f"fa-outsider-{frappe.generate_hash(length=8)}@example.com"
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": outsider,
				"first_name": "FA Outsider",
				"user_type": "System User",
				"send_welcome_email": 0,
				"roles": [{"role": AUDITOR_ROLE}],
			}
		)
		user.flags.ignore_permissions = True
		user.insert(ignore_permissions=True)

		with as_user(outsider):
			with self.assertRaises(frappe.PermissionError):
				list_conversations()

	def row(self, user: str, conversation: str) -> dict:
		for row in self.listed(user):
			if row["name"] == conversation:
				return row
		raise AssertionError(f"{conversation} is not in the rail")
