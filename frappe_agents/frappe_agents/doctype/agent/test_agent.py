# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe

from frappe_agents.tests.compat import IntegrationTestCase


class TestAgent(IntegrationTestCase):
	def test_defaults_to_session_user(self):
		meta = frappe.get_meta("Agent")
		self.assertEqual(meta.get_field("run_as").default, "Session User")
		self.assertEqual(meta.get_field("enabled").default, "0")
