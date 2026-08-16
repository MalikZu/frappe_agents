# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe_agents.tests.compat import IntegrationTestCase


class TestAgentSettings(IntegrationTestCase):
	def test_kill_switch_defaults_to_on(self):
		meta = frappe.get_meta("Agent Settings")
		self.assertTrue(meta.issingle)
		self.assertEqual(meta.get_field("global_enabled").default, "1")

	def test_no_field_is_sensitive_until_a_site_says_so(self):
		field = frappe.get_meta("Agent Settings").get_field("sensitive_fields")
		self.assertEqual(field.options, "Agent Sensitive Field")
		# The single's CURRENT rows are site state — another test's committed
		# fixture may legitimately be there. The shipped default is what must be
		# empty: no default value, no rows created by install.
		self.assertFalse(field.default)
		settings = frappe.get_single("Agent Settings")
		settings.set("sensitive_fields", [])
		settings.save(ignore_permissions=True)
		self.assertFalse(frappe.get_single("Agent Settings").sensitive_fields)

	def test_extraction_caps_have_defaults(self):
		meta = frappe.get_meta("Agent Settings")
		self.assertEqual(meta.get_field("max_extraction_pages").default, "20")
		self.assertEqual(meta.get_field("max_extraction_file_mb").default, "10")
		self.assertEqual(meta.get_field("extractions_per_user_per_day").default, "50")
