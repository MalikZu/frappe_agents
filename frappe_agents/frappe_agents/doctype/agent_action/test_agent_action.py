# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe_agents.tests.compat import IntegrationTestCase


class TestAgentAction(IntegrationTestCase):
	def test_no_role_may_write_an_action(self):
		for perm in frappe.get_meta("Agent Action").permissions:
			self.assertFalse(perm.create, perm.role)
			self.assertFalse(perm.write, perm.role)
			self.assertFalse(perm.delete, perm.role)

	def test_approver_and_auditor_read_every_action(self):
		roles = {p.role: p for p in frappe.get_meta("Agent Action").permissions}

		for role in ("Agent Approver", "Agent Auditor"):
			self.assertTrue(roles[role].read, role)
			self.assertFalse(roles[role].if_owner, role)

	def test_review_quality_readers_may_run_a_report(self):
		roles = {p.role: p for p in frappe.get_meta("Agent Action").permissions}

		for role in ("Agent Manager", "Agent Auditor"):
			self.assertTrue(roles[role].report, role)

	def test_agent_user_sees_only_own_actions(self):
		perms = [p for p in frappe.get_meta("Agent Action").permissions if p.role == "Agent User"]
		self.assertEqual(len(perms), 1)
		self.assertTrue(perms[0].if_owner)

	def test_decisions_are_tracked(self):
		self.assertTrue(frappe.get_meta("Agent Action").track_changes)
