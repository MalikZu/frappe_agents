# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe.tests import IntegrationTestCase


class TestAgentSettings(IntegrationTestCase):
	def test_kill_switch_defaults_to_on(self):
		meta = frappe.get_meta("Agent Settings")
		self.assertTrue(meta.issingle)
		self.assertEqual(meta.get_field("global_enabled").default, "1")
