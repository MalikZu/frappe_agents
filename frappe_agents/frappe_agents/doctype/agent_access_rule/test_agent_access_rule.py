# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe

from frappe_agents.tests.compat import IntegrationTestCase


class TestAgentAccessRule(IntegrationTestCase):
	def test_is_a_child_table(self):
		self.assertTrue(frappe.get_meta("Agent Access Rule").istable)

	def test_the_target_is_a_dynamic_link_on_the_target_type(self):
		field = frappe.get_meta("Agent Access Rule").get_field("target")
		self.assertEqual(field.fieldtype, "Dynamic Link")
		self.assertEqual(field.options, "target_type")
