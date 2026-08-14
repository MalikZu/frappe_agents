# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Choosing the model a conversation runs on.

Two parties decide, and a choice has to satisfy both. The agent's owner says
which profiles the agent may run — its default plus the alternates listed on the
record. The profile says which roles may run it. Neither is the chatting user, so
the tests here are mostly refusals, and every refusal has to leave nothing behind.

The wall is on the Agent Conversation document rather than beside the endpoint,
because the field belongs to a doctype its owner may write: `frappe.client` is a
door too.
"""

from unittest.mock import patch

import frappe

from frappe_agents.api import get_conversation, set_conversation_model, start_run
from frappe_agents.tests.fixtures import (
	AGENT,
	ALT_PROFILE,
	DRAFTER_ROLE,
	EXTRACT_PROFILE,
	GATED_PROFILE,
	OPEN_USER,
	PROFILE,
	RESTRICTED_USER,
	AgentTestCase,
	as_user,
	make_conversation,
)


class TestConversationModel(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.conversations = frappe.db.count("Agent Conversation")
		self.runs = frappe.db.count("Agent Run")

	def assert_nothing_was_written(self) -> None:
		self.assertEqual(frappe.db.count("Agent Conversation"), self.conversations)
		self.assertEqual(frappe.db.count("Agent Run"), self.runs)

	def start(self, user: str = RESTRICTED_USER, **values) -> dict:
		with as_user(user), patch("frappe.enqueue"):
			return start_run(agent=AGENT, message="Which model are you?", **values)

	def test_a_conversation_starts_on_the_agents_default(self):
		started = self.start()

		self.assertIsNone(frappe.db.get_value("Agent Conversation", started["conversation"], "model_profile"))
		# Nothing is stamped on the run either: the runner records what it ran.
		self.assertFalse(frappe.db.get_value("Agent Run", started["run"], "model_profile"))

	def test_an_alternate_is_pinned_to_the_conversation_and_the_run(self):
		started = self.start(model_profile=ALT_PROFILE)

		self.assertEqual(
			frappe.db.get_value("Agent Conversation", started["conversation"], "model_profile"),
			ALT_PROFILE,
		)
		self.assertEqual(frappe.db.get_value("Agent Run", started["run"], "model_profile"), ALT_PROFILE)

	def test_a_profile_the_agent_never_listed_is_refused(self):
		with as_user(RESTRICTED_USER), patch("frappe.enqueue") as enqueue:
			with self.assertRaises(frappe.ValidationError):
				start_run(agent=AGENT, message="Use the other one", model_profile=EXTRACT_PROFILE)
			enqueue.assert_not_called()
		self.assert_nothing_was_written()

	def test_a_profile_the_users_roles_fail_is_refused(self):
		with as_user(RESTRICTED_USER), patch("frappe.enqueue") as enqueue:
			with self.assertRaises(frappe.PermissionError):
				start_run(agent=AGENT, message="Use the good one", model_profile=GATED_PROFILE)
			enqueue.assert_not_called()
		self.assert_nothing_was_written()

	def test_a_gated_profile_is_accepted_from_a_user_who_holds_the_role(self):
		started = self.start(user=OPEN_USER, model_profile=GATED_PROFILE)

		self.assertEqual(frappe.db.get_value("Agent Run", started["run"], "model_profile"), GATED_PROFILE)

	def test_the_agents_own_default_may_be_named(self):
		"""It is what runs anyway. Refusing to let someone say so would be theatre."""
		started = self.start(model_profile=PROFILE)

		self.assertEqual(frappe.db.get_value("Agent Run", started["run"], "model_profile"), PROFILE)

	def test_the_pin_carries_to_the_next_turn(self):
		started = self.start(model_profile=ALT_PROFILE)
		again = self.start(conversation=started["conversation"])

		self.assertEqual(again["conversation"], started["conversation"])
		self.assertEqual(frappe.db.get_value("Agent Run", again["run"], "model_profile"), ALT_PROFILE)

	def test_a_pin_that_stopped_being_allowed_refuses_the_next_turn(self):
		"""Roles move. A conversation that already chose is not exempt from the check."""
		started = self.start(user=OPEN_USER, model_profile=GATED_PROFILE)
		self.gate_profile_on(GATED_PROFILE, DRAFTER_ROLE)

		with as_user(OPEN_USER), patch("frappe.enqueue") as enqueue:
			with self.assertRaises(frappe.PermissionError):
				start_run(agent=AGENT, message="Carry on", conversation=started["conversation"])
			enqueue.assert_not_called()

	def test_a_pin_written_straight_to_the_database_is_still_checked(self):
		"""`db.set_value` skips the controller entirely. The run door has to catch it.

		The controller is the wall for anything that saves a document. Nothing saves
		here — the value arrives as raw SQL, the way a patch or an import writes one —
		so the only thing between it and a provider is the check the next turn makes.
		"""
		started = self.start()
		frappe.db.set_value("Agent Conversation", started["conversation"], "model_profile", GATED_PROFILE)

		with as_user(RESTRICTED_USER), patch("frappe.enqueue") as enqueue:
			with self.assertRaises(frappe.PermissionError):
				start_run(agent=AGENT, message="Carry on", conversation=started["conversation"])
			enqueue.assert_not_called()

		self.assertEqual(
			frappe.db.count("Agent Run", {"conversation": started["conversation"]}),
			1,
			"the refused turn left a run behind",
		)

	def test_a_pin_the_agent_stopped_offering_refuses_the_next_turn(self):
		"""The owner's half of the wall moves too, and a conversation is not exempt."""
		started = self.start(model_profile=ALT_PROFILE)
		self.set_alternates(AGENT, [])

		with as_user(RESTRICTED_USER), patch("frappe.enqueue") as enqueue:
			with self.assertRaises(frappe.ValidationError):
				start_run(agent=AGENT, message="Carry on", conversation=started["conversation"])
			enqueue.assert_not_called()

	def set_alternates(self, agent: str, profiles: list[str]) -> None:
		"""Rewrite an agent's alternates, and put them back afterwards."""
		doc = frappe.get_doc("Agent", agent)
		before = [row.model_profile for row in doc.get("alternate_profiles") or []]
		self.addCleanup(self.write_alternates, agent, before)
		self.write_alternates(agent, profiles)

	def write_alternates(self, agent: str, profiles: list[str]) -> None:
		doc = frappe.get_doc("Agent", agent)
		doc.set("alternate_profiles", [{"model_profile": profile} for profile in profiles])
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)
		frappe.clear_document_cache("Agent", agent)

	def gate_profile_on(self, profile: str, role: str) -> None:
		"""Move a profile behind a role, and put it back afterwards."""
		doc = frappe.get_doc("LLM Model Profile", profile)
		before = [row.role for row in doc.get("allowed_roles") or []]
		self.addCleanup(self.restore_profile_roles, profile, before)

		doc.set("allowed_roles", [{"role": role}])
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)
		frappe.clear_document_cache("LLM Model Profile", profile)

	def restore_profile_roles(self, profile: str, roles: list[str]) -> None:
		doc = frappe.get_doc("LLM Model Profile", profile)
		doc.set("allowed_roles", [{"role": role} for role in roles])
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)
		frappe.clear_document_cache("LLM Model Profile", profile)

	def test_set_conversation_model_pins_and_unpins(self):
		conversation = make_conversation(RESTRICTED_USER)

		with as_user(RESTRICTED_USER):
			set_conversation_model(conversation.name, ALT_PROFILE)
			self.assertEqual(get_conversation(conversation.name)["model_profile"], ALT_PROFILE)

			set_conversation_model(conversation.name, None)
			self.assertIsNone(get_conversation(conversation.name)["model_profile"])

	def test_set_conversation_model_refuses_a_profile_the_agent_never_listed(self):
		conversation = make_conversation(RESTRICTED_USER)

		with as_user(RESTRICTED_USER):
			with self.assertRaises(frappe.ValidationError):
				set_conversation_model(conversation.name, EXTRACT_PROFILE)

		self.assertIsNone(frappe.db.get_value("Agent Conversation", conversation.name, "model_profile"))

	def test_set_conversation_model_refuses_someone_elses_conversation(self):
		conversation = make_conversation(OPEN_USER)

		with as_user(RESTRICTED_USER):
			with self.assertRaises(frappe.PermissionError):
				set_conversation_model(conversation.name, ALT_PROFILE)

	def test_the_wall_holds_when_the_document_is_written_directly(self):
		"""The endpoint is one door. The check has to live on the document."""
		conversation = make_conversation(RESTRICTED_USER)

		with as_user(RESTRICTED_USER):
			doc = frappe.get_doc("Agent Conversation", conversation.name)
			doc.model_profile = EXTRACT_PROFILE
			with self.assertRaises(frappe.ValidationError):
				doc.save()
