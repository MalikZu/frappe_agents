# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe_agents.tests.compat import IntegrationTestCase


class TestAgentAlternateProfile(IntegrationTestCase):
	def test_is_a_child_table(self):
		self.assertTrue(frappe.get_meta("Agent Alternate Profile").istable)
