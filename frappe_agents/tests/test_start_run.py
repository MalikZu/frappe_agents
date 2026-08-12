# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The chat entry point.

`start_run` never executes anything: it decides whether this user may talk to
this agent, then queues a run stamped with the identity it must execute under.
The queue itself is patched out — this is about what gets written, not about RQ.
"""

from unittest.mock import patch

import frappe

from frappe_agents.api import list_agents, start_run
from frappe_agents.tests.fixtures import (
	AGENT,
	OPEN_USER,
	PERMLEVEL_ROLE,
	PROFILE,
	RESTRICTED_USER,
	AgentTestCase,
	as_user,
)


class TestStartRun(AgentTestCase):
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

	def test_queues_a_run_owned_by_the_session_user(self):
		with as_user(RESTRICTED_USER), patch("frappe.enqueue") as enqueue:
			result = start_run(agent=AGENT, message="How many tickets are open?")

		enqueue.assert_called_once()
		self.assertEqual(enqueue.call_args.kwargs["run_name"], result["run"])

		values = frappe.db.get_value(
			"Agent Run",
			result["run"],
			["status", "effective_user", "owner", "run_as_mode", "conversation", "input_message"],
			as_dict=True,
		)
		self.assertEqual(values.status, "Queued")
		self.assertEqual(values.effective_user, RESTRICTED_USER)
		self.assertEqual(values.owner, RESTRICTED_USER)
		self.assertEqual(values.run_as_mode, "Session User")
		self.assertEqual(values.conversation, result["conversation"])
		self.assertEqual(values.input_message, "How many tickets are open?")

		conversation = frappe.db.get_value(
			"Agent Conversation", result["conversation"], ["user", "owner", "agent"], as_dict=True
		)
		self.assertEqual(conversation.user, RESTRICTED_USER)
		self.assertEqual(conversation.owner, RESTRICTED_USER)
		self.assertEqual(conversation.agent, AGENT)

	def test_service_user_agents_cannot_be_started_from_chat(self):
		name = self.make_agent("Service Agent", run_as="Service User", service_user=OPEN_USER)

		with as_user(RESTRICTED_USER), patch("frappe.enqueue") as enqueue:
			with self.assertRaises(frappe.ValidationError):
				start_run(agent=name, message="Run as someone else, please")
			enqueue.assert_not_called()

	def test_agent_is_gated_on_its_allowed_roles(self):
		name = self.make_agent("Gated Agent", allowed_roles=[{"role": PERMLEVEL_ROLE}])

		with as_user(RESTRICTED_USER), patch("frappe.enqueue") as enqueue:
			with self.assertRaises(frappe.PermissionError):
				start_run(agent=name, message="Let me in")
			enqueue.assert_not_called()

		with as_user(OPEN_USER), patch("frappe.enqueue"):
			result = start_run(agent=name, message="I hold the role")
		self.assertTrue(result["run"])

	def test_list_agents_hides_agents_the_user_may_not_use(self):
		name = self.make_agent("Gated Agent", allowed_roles=[{"role": PERMLEVEL_ROLE}])

		with as_user(RESTRICTED_USER):
			visible = {row["name"] for row in list_agents()}
		self.assertIn(AGENT, visible)
		self.assertNotIn(name, visible)

		with as_user(OPEN_USER):
			visible = {row["name"] for row in list_agents()}
		self.assertIn(name, visible)

	def test_empty_message_is_refused(self):
		with as_user(RESTRICTED_USER), patch("frappe.enqueue") as enqueue:
			with self.assertRaises(frappe.ValidationError):
				start_run(agent=AGENT, message="   ")
			enqueue.assert_not_called()
