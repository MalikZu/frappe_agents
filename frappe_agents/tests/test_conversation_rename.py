# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Naming a conversation.

The default name is the first thing the user typed. This is how they replace it,
and it is the only way a title changes: no model writes one, because a title a
model wrote is model-written memory arriving through the back door.
"""

import frappe

from frappe_agents.api import TITLE_CHARS, list_conversations, rename_conversation
from frappe_agents.tests.fixtures import (
	OPEN_USER,
	RESTRICTED_USER,
	AgentTestCase,
	as_user,
	make_conversation,
)


class TestConversationRename(AgentTestCase):
	def test_the_owner_may_rename_their_conversation(self):
		conversation = make_conversation(RESTRICTED_USER, title="How many tickets are open?")

		with as_user(RESTRICTED_USER):
			result = rename_conversation(conversation.name, "  Ticket triage  ")
			rows = {row["name"]: row for row in list_conversations()}

		self.assertEqual(result["title"], "Ticket triage")
		self.assertEqual(
			frappe.db.get_value("Agent Conversation", conversation.name, "title"), "Ticket triage"
		)
		self.assertEqual(rows[conversation.name]["snippet"], "Ticket triage")

	def test_an_empty_title_is_refused(self):
		conversation = make_conversation(RESTRICTED_USER, title="Keep me")

		with as_user(RESTRICTED_USER):
			with self.assertRaises(frappe.ValidationError):
				rename_conversation(conversation.name, "   ")

		self.assertEqual(frappe.db.get_value("Agent Conversation", conversation.name, "title"), "Keep me")

	def test_an_overlong_title_is_refused(self):
		conversation = make_conversation(RESTRICTED_USER, title="Keep me")

		with as_user(RESTRICTED_USER):
			with self.assertRaises(frappe.ValidationError):
				rename_conversation(conversation.name, "x" * (TITLE_CHARS + 1))

		self.assertEqual(frappe.db.get_value("Agent Conversation", conversation.name, "title"), "Keep me")

	def test_another_users_conversation_cannot_be_renamed(self):
		conversation = make_conversation(OPEN_USER, title="Theirs")

		with as_user(RESTRICTED_USER):
			with self.assertRaises(frappe.PermissionError):
				rename_conversation(conversation.name, "Mine now")

		self.assertEqual(frappe.db.get_value("Agent Conversation", conversation.name, "title"), "Theirs")
