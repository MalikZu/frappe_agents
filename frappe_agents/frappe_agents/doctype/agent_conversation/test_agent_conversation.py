# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe.tests import IntegrationTestCase


class TestAgentConversation(IntegrationTestCase):
	def test_agent_user_sees_only_own_conversations(self):
		perms = [p for p in frappe.get_meta("Agent Conversation").permissions if p.role == "Agent User"]
		self.assertEqual(len(perms), 1)
		self.assertTrue(perms[0].if_owner)
