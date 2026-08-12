# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe.tests import IntegrationTestCase


class TestAgentRun(IntegrationTestCase):
	def test_no_role_may_write_a_run(self):
		for perm in frappe.get_meta("Agent Run").permissions:
			self.assertFalse(perm.create, perm.role)
			self.assertFalse(perm.write, perm.role)
			self.assertFalse(perm.delete, perm.role)
