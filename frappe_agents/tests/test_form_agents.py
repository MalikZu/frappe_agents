# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The list of agents a form may offer.

The button on a form decides whether to appear before it can afford a server
call, so the list rides along with the desk boot. It has to be the same list the
chat endpoint would allow — enabled, marked for forms, running as the session
user, and permitted to this user — and it has to survive a site whose migration
has not run yet, because an exception here breaks the desk.
"""

from unittest.mock import patch

import frappe

from frappe_agents.boot import boot_form_agents
from frappe_agents.tests.fixtures import (
	AGENT,
	OPEN_USER,
	PERMLEVEL_ROLE,
	PROFILE,
	READER_ROLE,
	RESTRICTED_USER,
	TICKET_DT,
	AgentTestCase,
	as_user,
)


class MetaMissingField:
	"""A Meta that has forgotten one field: a site whose migration has not run yet."""

	def __init__(self, meta, fieldname: str):
		self._meta = meta
		self._fieldname = fieldname

	def has_field(self, fieldname: str) -> bool:
		return False if fieldname == self._fieldname else self._meta.has_field(fieldname)

	def __getattr__(self, attr):
		return getattr(self._meta, attr)


class TestFormAgents(AgentTestCase):
	def make_agent(self, label: str, **values) -> str:
		agent = frappe.get_doc(
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
		agent.flags.ignore_permissions = True
		agent.insert(ignore_permissions=True)
		return agent.name

	def form_agents(self, user: str) -> list[dict]:
		bootinfo: dict = {}
		with as_user(user):
			boot_form_agents(bootinfo)
		return bootinfo["frappe_agents"]["form_agents"]

	def names(self, user: str) -> set:
		return {entry["name"] for entry in self.form_agents(user)}

	def entry_for(self, user: str, name: str) -> dict:
		for entry in self.form_agents(user):
			if entry["name"] == name:
				return entry
		self.fail(f"{name} not in {self.names(user)}")

	def test_only_agents_marked_for_forms_are_offered(self):
		offered = self.make_agent("Form Agent", show_in_forms=1)
		chat_only = self.make_agent("Chat Agent")

		names = self.names(RESTRICTED_USER)
		self.assertIn(offered, names)
		self.assertNotIn(chat_only, names)
		# The shared fixture agent is a chat agent, and stays one.
		self.assertNotIn(AGENT, names)

	def test_a_disabled_agent_is_not_offered(self):
		disabled = self.make_agent("Disabled Form Agent", show_in_forms=1, enabled=0)
		self.assertNotIn(disabled, self.names(RESTRICTED_USER))

	def test_a_service_user_agent_is_not_offered(self):
		"""A form run is the user asking about their own document, as themselves."""
		service = self.make_agent(
			"Service Form Agent", show_in_forms=1, run_as="Service User", service_user=OPEN_USER
		)
		self.assertNotIn(service, self.names(RESTRICTED_USER))

	def test_an_agent_gated_on_a_role_is_offered_only_to_that_role(self):
		gated = self.make_agent("Gated Form Agent", show_in_forms=1, allowed_roles=[{"role": PERMLEVEL_ROLE}])

		self.assertNotIn(gated, self.names(RESTRICTED_USER))
		self.assertIn(gated, self.names(OPEN_USER))

	def test_the_doctypes_an_agent_covers_ride_along(self):
		scoped = self.make_agent(
			"Scoped Form Agent", show_in_forms=1, form_doctypes=[{"document_type": TICKET_DT}]
		)
		everywhere = self.make_agent("Open Form Agent", show_in_forms=1)

		self.assertEqual(self.entry_for(RESTRICTED_USER, scoped)["doctypes"], [TICKET_DT])
		# Empty means every doctype, not none.
		self.assertEqual(self.entry_for(RESTRICTED_USER, everywhere)["doctypes"], [])

	def test_a_user_without_the_agent_role_is_offered_nothing(self):
		self.make_agent("Form Agent", show_in_forms=1)
		email = f"fa-outsider-{frappe.generate_hash(length=6)}@example.com"
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "FA Outsider",
				"user_type": "System User",
				"send_welcome_email": 0,
				"roles": [{"role": READER_ROLE}],
			}
		)
		user.flags.ignore_permissions = True
		user.insert(ignore_permissions=True)
		frappe.clear_cache(user=email)

		self.assertEqual(self.form_agents(email), [])

	def test_guest_is_offered_nothing(self):
		self.make_agent("Form Agent", show_in_forms=1)
		self.assertEqual(self.form_agents("Guest"), [])

	def test_a_site_without_the_field_yet_is_offered_nothing(self):
		self.make_agent("Form Agent", show_in_forms=1)
		real_get_meta = frappe.get_meta

		def get_meta(doctype, *args, **kwargs):
			meta = real_get_meta(doctype, *args, **kwargs)
			return MetaMissingField(meta, "show_in_forms") if doctype == "Agent" else meta

		with patch("frappe.get_meta", side_effect=get_meta):
			self.assertEqual(self.form_agents(RESTRICTED_USER), [])

	def test_a_failure_never_breaks_the_desk(self):
		bootinfo: dict = {}
		with patch("frappe_agents.boot._form_agents", side_effect=RuntimeError("boom")):
			boot_form_agents(bootinfo)

		self.assertEqual(bootinfo["frappe_agents"]["form_agents"], [])
