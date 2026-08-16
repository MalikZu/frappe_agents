# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe_agents.tests.compat import IntegrationTestCase


class TestAgentToolCall(IntegrationTestCase):
	def test_audit_rows_are_append_only(self):
		for perm in frappe.get_meta("Agent Tool Call").permissions:
			self.assertFalse(perm.write, perm.role)
			self.assertFalse(perm.delete, perm.role)
