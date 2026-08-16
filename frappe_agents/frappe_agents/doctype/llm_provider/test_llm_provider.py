# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe_agents.tests.compat import IntegrationTestCase


class TestLLMProvider(IntegrationTestCase):
	def test_api_key_is_a_password_field(self):
		meta = frappe.get_meta("LLM Provider")
		self.assertEqual(meta.get_field("api_key").fieldtype, "Password")
