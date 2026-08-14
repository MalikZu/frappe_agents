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

from frappe_agents.api import (
	CONVERSATION_BYTE_BUDGET,
	CONVERSATION_RUN_LIMIT,
	get_conversation,
	start_run,
)
from frappe_agents.tests.fixtures import (
	AGENT,
	RESTRICTED_USER,
	AgentTestCase,
	as_user,
	make_conversation,
	make_run,
)

# The payload as of 0.3.0, before the rail, the model chip and the context chip.
LEGACY_KEYS = {"name", "agent", "title", "last_activity", "runs"}
ADDED_KEYS = {"agent_name", "model_profile", "context"}
# Added on purpose after the 2026-08-14 review found this endpoint returning
# every run of a conversation, however long it had grown. `runs` is now the end
# of the conversation rather than all of it, and these two keys are how a caller
# is told so: `truncated` that older turns exist, `next_before` what to pass as
# `before` to read them. A caller that ignores both still gets runs it can draw.
PAGE_KEYS = {"truncated", "next_before"}
# Added the same way, and deliberately outside the paging: `attachments` is the
# conversation's own files, not the page's. A file uploaded in chat is attached to
# the conversation and never deleted, but nothing in the payload said so, so a
# reload left it unreachable. Empty list for a conversation nobody attached
# anything to — present and empty, never missing.
ATTACHMENT_KEYS = {"attachments"}
LEGACY_RUN_KEYS = {
	"name",
	"status",
	"input_message",
	"output_message",
	"error",
	"creation",
	"event_log",
}
# What a run says about the documents it had read. Added the same way and under
# the same promise: present and empty for every run that read nothing.
ADDED_RUN_KEYS = {"extractions"}


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

	def test_nothing_was_added_beyond_the_keys_that_were_meant_to_be(self):
		"""A key nobody meant to add is a key someone will come to depend on."""
		conversation, _ = self.legacy_conversation()

		self.assertEqual(set(self.read(conversation)), LEGACY_KEYS | ADDED_KEYS | PAGE_KEYS | ATTACHMENT_KEYS)

	def test_a_short_conversation_says_it_is_all_there(self):
		"""The paging keys are answers, not conditions: one turn is a whole page."""
		conversation, _ = self.legacy_conversation()

		payload = self.read(conversation)

		self.assertFalse(payload["truncated"])
		self.assertIsNone(payload["next_before"])

	def test_the_new_keys_are_present_and_empty(self):
		conversation, _ = self.legacy_conversation()

		payload = self.read(conversation)

		self.assertIsNone(payload["model_profile"])
		self.assertIsNone(payload["context"])
		self.assertEqual(payload["attachments"], [])
		# The one that is not empty: it is the agent's label, and every conversation
		# has always had an agent to take it from.
		self.assertEqual(payload["agent_name"], AGENT)

	def test_a_run_carries_the_same_fields_it_always_did(self):
		conversation, run = self.legacy_conversation()

		rows = self.read(conversation)["runs"]

		self.assertEqual(len(rows), 1)
		self.assertEqual(set(rows[0]), LEGACY_RUN_KEYS | ADDED_RUN_KEYS)
		self.assertEqual(rows[0]["name"], run)
		self.assertEqual(rows[0]["input_message"], "How many tickets are open?")
		self.assertEqual(rows[0]["output_message"], "Two of them.")
		# Still parsed, still a list, still empty when the run never wrote a log.
		self.assertEqual(rows[0]["event_log"], [])
		# A run from before extraction existed read nothing, and says so.
		self.assertEqual(rows[0]["extractions"], [])


def big_log(chars: int, turn: int) -> str:
	"""An event log of about `chars`, shaped the way the runner writes one."""
	return frappe.as_json({"events": [{"type": "tool_result", "text": f"{turn}:" + "x" * chars}]})


class TestConversationPaging(AgentTestCase):
	"""A conversation nobody ever stops talking in still comes back as one answer.

	Two bounds and not one: a run count, because a conversation has no ceiling,
	and a byte budget, because one run's event log is allowed half a megabyte and
	twenty of those are not an answer anybody can render. The page is the end of
	the conversation — what a person came back to read — and it says what it left
	out and how to ask for it.
	"""

	def long_conversation(self, turns: int, log_chars: int = 200) -> str:
		"""`turns` runs in one conversation, on a timeline of their own.

		The creations are written by hand so the order under test is the order the
		test asked for, not whatever the clock did during a fast insert loop.
		"""
		conversation = make_conversation(RESTRICTED_USER, title="")
		with as_user(RESTRICTED_USER):
			for turn in range(1, turns + 1):
				run = make_run(RESTRICTED_USER, conversation=conversation.name, message=f"Turn {turn}")
				frappe.db.set_value(
					"Agent Run",
					run.name,
					{
						"status": "Completed",
						"output_message": f"Answer {turn}",
						"event_log": big_log(log_chars, turn),
						"creation": f"2026-01-01 00:{turn // 60:02d}:{turn % 60:02d}",
					},
					update_modified=False,
				)
		return conversation.name

	def read(self, conversation: str, **args) -> dict:
		with as_user(RESTRICTED_USER):
			return get_conversation(conversation, **args)

	def test_conversation_payload_is_bounded_and_paginated(self):
		turns = CONVERSATION_RUN_LIMIT + 5
		# Big enough that the byte budget bites before the run count does, which is
		# the case a run count alone would miss.
		conversation = self.long_conversation(turns, log_chars=CONVERSATION_BYTE_BUDGET // 16)

		payload = self.read(conversation)
		runs = payload["runs"]

		self.assertLessEqual(len(runs), CONVERSATION_RUN_LIMIT)
		self.assertLess(len(runs), turns)
		self.assertLessEqual(len(frappe.as_json(payload)), CONVERSATION_BYTE_BUDGET * 2)

		# Oldest first, the way the surface draws them, and ending at the newest
		# turn: what was dropped was dropped from the far end.
		said = [run["input_message"] for run in runs]
		self.assertEqual(said, sorted(said, key=lambda text: int(text.split()[1])))
		self.assertEqual(said[-1], f"Turn {turns}")

		self.assertTrue(payload["truncated"])
		self.assertTrue(payload["next_before"])

	def test_the_continuation_marker_reads_the_turns_above_the_page(self):
		turns = CONVERSATION_RUN_LIMIT + 5
		conversation = self.long_conversation(turns, log_chars=CONVERSATION_BYTE_BUDGET // 16)

		page = self.read(conversation)
		older = self.read(conversation, before=page["next_before"])

		self.assertTrue(older["runs"])
		# No turn is read twice and none is skipped: the two pages are the
		# conversation, in order.
		first = {run["name"] for run in page["runs"]}
		second = {run["name"] for run in older["runs"]}
		self.assertFalse(first & second)
		self.assertEqual(len(first | second), turns)
		self.assertFalse(older["truncated"])

	def test_a_page_is_never_more_runs_than_the_limit(self):
		"""The byte budget can leave a page short. The count can never leave it long."""
		turns = CONVERSATION_RUN_LIMIT + 5
		conversation = self.long_conversation(turns, log_chars=50)

		payload = self.read(conversation)

		self.assertEqual(len(payload["runs"]), CONVERSATION_RUN_LIMIT)
		self.assertTrue(payload["truncated"])
