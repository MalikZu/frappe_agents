# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe.tests import IntegrationTestCase


class TestLLMModelProfile(IntegrationTestCase):
	def test_named_by_profile_name(self):
		self.assertEqual(frappe.get_meta("LLM Model Profile").autoname, "field:profile_name")

	def test_file_capabilities_are_off_until_declared(self):
		meta = frappe.get_meta("LLM Model Profile")

		for fieldname in ("supports_pdf", "supports_images"):
			field = meta.get_field(fieldname)
			self.assertEqual(field.fieldtype, "Check", fieldname)
			self.assertEqual(field.default, "0", fieldname)
