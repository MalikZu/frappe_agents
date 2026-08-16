# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe_agents.tests.compat import IntegrationTestCase


class TestAgentSkill(IntegrationTestCase):
	def test_defaults_to_draft(self):
		meta = frappe.get_meta("Agent Skill")
		self.assertEqual(meta.get_field("status").default, "Draft")
		self.assertTrue(meta.get_field("approved_by").read_only)
