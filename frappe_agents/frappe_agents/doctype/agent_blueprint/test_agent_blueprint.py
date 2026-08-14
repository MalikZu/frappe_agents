# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe.tests import IntegrationTestCase


class TestAgentBlueprint(IntegrationTestCase):
	def test_a_new_blueprint_is_a_draft(self):
		self.assertEqual(frappe.get_meta("Agent Blueprint").get_field("status").default, "Draft")

	def test_the_created_links_are_read_only(self):
		meta = frappe.get_meta("Agent Blueprint")
		for fieldname in ("created_agent", "created_profile"):
			self.assertTrue(meta.get_field(fieldname).read_only, fieldname)
