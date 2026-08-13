# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe.tests import IntegrationTestCase


class TestAgentSensitiveField(IntegrationTestCase):
	def test_is_a_child_table(self):
		self.assertTrue(frappe.get_meta("Agent Sensitive Field").istable)

	def test_unknown_fieldname_is_refused(self):
		row = frappe.new_doc("Agent Sensitive Field")
		row.document_type = "User"
		row.fieldname = "not_a_real_field"

		self.assertRaises(frappe.ValidationError, row.validate)

	def test_known_fieldname_passes(self):
		row = frappe.new_doc("Agent Sensitive Field")
		row.document_type = "User"
		row.fieldname = " email "
		row.validate()

		self.assertEqual(row.fieldname, "email")
