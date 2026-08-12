# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe.tests import IntegrationTestCase


class TestAgentAllowedRole(IntegrationTestCase):
	def test_is_a_child_table(self):
		self.assertTrue(frappe.get_meta("Agent Allowed Role").istable)
