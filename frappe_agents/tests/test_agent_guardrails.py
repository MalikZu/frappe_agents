# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""What an Agent record refuses to be.

A service user runs with nobody watching, so the Agent doctype will not accept a
privileged one: not Administrator, not Guest, not a System Manager. This is the
one place the permission model is enforced before anything runs at all.
"""

import frappe

from frappe_agents.tests.fixtures import OPEN_USER, PROFILE, AgentTestCase


class TestAgentGuardrails(AgentTestCase):
	def agent_doc(self, label: str, **values):
		return frappe.get_doc(
			{
				"doctype": "Agent",
				"agent_name": f"FA {label} {frappe.generate_hash(length=6)}",
				"enabled": 1,
				"run_as": "Session User",
				"model_profile": PROFILE,
				"autonomy": "Suggest",
				"max_steps": 3,
				**values,
			}
		)

	def test_administrator_cannot_be_a_service_user(self):
		agent = self.agent_doc("Admin Agent", run_as="Service User", service_user="Administrator")
		self.assertRaises(frappe.ValidationError, agent.insert)
		self.assertFalse(frappe.db.exists("Agent", agent.agent_name))

	def test_guest_cannot_be_a_service_user(self):
		agent = self.agent_doc("Guest Agent", run_as="Service User", service_user="Guest")
		self.assertRaises(frappe.ValidationError, agent.insert)

	def test_service_user_is_required(self):
		agent = self.agent_doc("Headless Agent", run_as="Service User")
		self.assertRaises(frappe.ValidationError, agent.insert)

	def test_system_manager_cannot_be_a_service_user(self):
		email = f"fa-sysmanager-{frappe.generate_hash(length=6)}@example.com"
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "FA System Manager",
				"user_type": "System User",
				"send_welcome_email": 0,
				"roles": [{"role": "System Manager"}],
			}
		)
		user.flags.ignore_permissions = True
		user.insert(ignore_permissions=True)

		agent = self.agent_doc("Privileged Agent", run_as="Service User", service_user=email)
		self.assertRaises(frappe.ValidationError, agent.insert)

	def test_an_enabled_agent_cannot_be_switched_to_administrator(self):
		agent = self.agent_doc("Switchable Agent", run_as="Service User", service_user=OPEN_USER)
		agent.insert(ignore_permissions=True)

		agent.service_user = "Administrator"
		self.assertRaises(frappe.ValidationError, agent.save)
		self.assertEqual(frappe.db.get_value("Agent", agent.name, "service_user"), OPEN_USER)

	def test_a_narrow_service_user_is_accepted(self):
		"""The positive control: an ordinary user is a legal service user."""
		agent = self.agent_doc("Narrow Agent", run_as="Service User", service_user=OPEN_USER)
		agent.insert(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("Agent", agent.name, "enabled"), 1)

	def test_a_session_user_agent_needs_no_service_user(self):
		agent = self.agent_doc("Session Agent")
		agent.insert(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("Agent", agent.name, "run_as"), "Session User")
