# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe.tests import IntegrationTestCase


class TestAgentTool(IntegrationTestCase):
	def test_registry_is_read_only_to_humans(self):
		for perm in frappe.get_meta("Agent Tool").permissions:
			self.assertFalse(perm.create, perm.role)
			self.assertFalse(perm.write, perm.role)
			self.assertFalse(perm.delete, perm.role)
