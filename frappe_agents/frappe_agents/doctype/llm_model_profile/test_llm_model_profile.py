# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe.tests import IntegrationTestCase


class TestLLMModelProfile(IntegrationTestCase):
	def test_named_by_profile_name(self):
		self.assertEqual(frappe.get_meta("LLM Model Profile").autoname, "field:profile_name")
