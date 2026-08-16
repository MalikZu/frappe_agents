# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe

from frappe_agents.tests.compat import IntegrationTestCase


class TestAgentAccessProfile(IntegrationTestCase):
	def test_a_profile_has_no_enabled_flag(self):
		"""A profile is inert until an agent attaches it. A switch would imply otherwise."""
		self.assertIsNone(frappe.get_meta("Agent Access Profile").get_field("enabled"))

	def test_it_holds_access_rules(self):
		field = frappe.get_meta("Agent Access Profile").get_field("rules")
		self.assertEqual(field.options, "Agent Access Rule")
