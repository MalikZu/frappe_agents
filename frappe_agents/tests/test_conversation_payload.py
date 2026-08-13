# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""What `get_conversation` promises, for a conversation written before this release.

The chat surface and the endpoint ship together, so the surface can be trusted to
read whatever the endpoint now returns. Nothing else can: `get_conversation` is
whitelisted, and a whitelisted method is a public interface whether or not anyone
outside this app happens to be calling it today.

So this is a characterization test. A conversation with no title, no pinned model
and no document — every one of them written before those fields existed — has to
come back with the keys it always came back with, carrying what they always
carried, and the three new keys have to be present and empty rather than missing.
A caller that ignores them keeps working; a caller that reads them gets a null,
not a KeyError.
"""

from unittest.mock import patch

import frappe

from frappe_agents.api import get_conversation, start_run
from frappe_agents.tests.fixtures import (
	AGENT,
	RESTRICTED_USER,
	AgentTestCase,
	as_user,
)

# The payload as of 0.3.0, before the rail, the model chip and the context chip.
LEGACY_KEYS = {"name", "agent", "title", "last_activity", "runs"}
ADDED_KEYS = {"agent_name", "model_profile", "context"}
LEGACY_RUN_KEYS = {
	"name",
	"status",
	"input_message",
	"output_message",
	"error",
	"creation",
	"event_log",
}


class TestConversationPayload(AgentTestCase):
	def legacy_conversation(self) -> tuple[str, str]:
		"""A conversation as an older release left it: untitled, unpinned, about nothing.

		Started through the endpoint, because the run has to be owned by the user
		who asks for it back — that is what the doctype's `if_owner` read means, and
		a run inserted by the test session would be invisible for the wrong reason.
		"""
		with as_user(RESTRICTED_USER), patch("frappe.enqueue"):
			started = start_run(agent=AGENT, message="How many tickets are open?")

		frappe.db.set_value(
			"Agent Conversation",
			started["conversation"],
			{"title": None, "model_profile": None},
			update_modified=False,
		)
		frappe.db.set_value(
			"Agent Run",
			started["run"],
			{"status": "Completed", "output_message": "Two of them."},
			update_modified=False,
		)
		return started["conversation"], started["run"]

	def read(self, conversation: str) -> dict:
		with as_user(RESTRICTED_USER):
			return get_conversation(conversation)

	def test_the_old_keys_are_all_still_there_and_still_mean_what_they_meant(self):
		conversation, _ = self.legacy_conversation()

		payload = self.read(conversation)

		self.assertLessEqual(LEGACY_KEYS, set(payload))
		self.assertEqual(payload["name"], conversation)
		self.assertEqual(payload["agent"], AGENT)
		self.assertFalse(payload["title"])
		self.assertIsInstance(payload["runs"], list)

	def test_nothing_was_added_beyond_the_three_new_keys(self):
		"""A key nobody meant to add is a key someone will come to depend on."""
		conversation, _ = self.legacy_conversation()

		self.assertEqual(set(self.read(conversation)), LEGACY_KEYS | ADDED_KEYS)

	def test_the_new_keys_are_present_and_empty(self):
		conversation, _ = self.legacy_conversation()

		payload = self.read(conversation)

		self.assertIsNone(payload["model_profile"])
		self.assertIsNone(payload["context"])
		# The one that is not empty: it is the agent's label, and every conversation
		# has always had an agent to take it from.
		self.assertEqual(payload["agent_name"], AGENT)

	def test_a_run_carries_the_same_fields_it_always_did(self):
		conversation, run = self.legacy_conversation()

		rows = self.read(conversation)["runs"]

		self.assertEqual(len(rows), 1)
		self.assertEqual(set(rows[0]), LEGACY_RUN_KEYS)
		self.assertEqual(rows[0]["name"], run)
		self.assertEqual(rows[0]["input_message"], "How many tickets are open?")
		self.assertEqual(rows[0]["output_message"], "Two of them.")
		# Still parsed, still a list, still empty when the run never wrote a log.
		self.assertEqual(rows[0]["event_log"], [])
