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

	def test_no_part_of_another_users_conversation_reaches_the_payload(self):
		"""Not just a missing row: the whole answer is searched for their words.

		A rail row is assembled from three places — the conversation, its agent and
		its first run — and any of them could have been read without the filter that
		the conversation itself passed through.
		"""
		theirs = self.start(OPEN_USER, "The vendor account is 4471 9930")
		self.start(RESTRICTED_USER, "Mine")

		blob = frappe.as_json(self.listed(RESTRICTED_USER))
		self.assertNotIn(theirs["conversation"], blob)
		self.assertNotIn("4471 9930", blob)

	def test_a_row_whose_owner_and_user_disagree_belongs_to_neither(self):
		"""Two filters, and a row has to pass both: the doctype's own if_owner rule
		and the query's filter on the user field. Passing one is not enough."""
		theirs = self.start(OPEN_USER, "Half theirs")
		frappe.db.set_value(
			"Agent Conversation", theirs["conversation"], "owner", RESTRICTED_USER, update_modified=False
		)

		for user in (RESTRICTED_USER, OPEN_USER):
			names = {row["name"] for row in self.listed(user)}
			self.assertNotIn(theirs["conversation"], names)

	def test_a_row_carries_the_agent_name_and_a_snippet(self):
		started = self.start(RESTRICTED_USER, "How many tickets are open?")

		row = self.row(RESTRICTED_USER, started["conversation"])
		self.assertEqual(row["agent"], AGENT)
		self.assertEqual(row["agent_name"], AGENT)
		self.assertEqual(row["snippet"], "How many tickets are open?")
		self.assertTrue(row["last_activity"])

	def test_a_conversation_without_a_title_falls_back_to_its_first_message(self):
		started = self.start(RESTRICTED_USER, "The first thing")
		self.start(RESTRICTED_USER, "The second thing", conversation=started["conversation"])
		frappe.db.set_value("Agent Conversation", started["conversation"], "title", "")

		row = self.row(RESTRICTED_USER, started["conversation"])
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

	def test_conversation_list_uses_bounded_previews(self):
		"""The rail's cost is its page, not the user's whole history.

		A snippet is one row per conversation — the earliest run, picked by the
		database. Reading every run of every conversation to keep the oldest one
		made a rail row cost more the longer the conversation was, so the rows the
		endpoint reads are counted here and not just the rows it returns.
		"""
		conversations = []
		for number in range(1, 4):
			started = self.start(RESTRICTED_USER, f"Opening line {number}")
			conversations.append(started["conversation"])
			for turn in range(4):
				self.start(
					RESTRICTED_USER, f"Later line {number}.{turn}", conversation=started["conversation"]
				)

		read = []
		original = frappe.db.sql

		def counted(query, *args, **kwargs):
			result = original(query, *args, **kwargs)
			if "tabAgent Run" in str(query):
				read.append(len(result) if hasattr(result, "__len__") else 0)
			return result

		with patch("frappe_agents.api.CONVERSATION_LIMIT", 2):
			with as_user(RESTRICTED_USER), patch.object(frappe.db, "sql", counted):
				listed = list_conversations()

		# The page is the page: three conversations exist, two were asked for.
		self.assertEqual(len(listed), 2)
		self.assertTrue(read, "no Agent Run query was observed")
		# Fifteen runs exist. A bounded preview reads one row per conversation on
		# the page — two, and the same again if two runs share an instant.
		self.assertLessEqual(max(read), 2 * len(listed))
		self.assertLessEqual(sum(read), 2 * len(listed))

		snippets = {row["name"]: row["snippet"] for row in listed}
		for name, snippet in snippets.items():
			self.assertEqual(snippet, f"Opening line {conversations.index(name) + 1}")

	def row(self, user: str, conversation: str) -> dict:
		for row in self.listed(user):
			if row["name"] == conversation:
				return row
		raise AssertionError(f"{conversation} is not in the rail")
