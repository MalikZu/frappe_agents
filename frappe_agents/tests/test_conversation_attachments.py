# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The files a conversation is carrying — on the payload, and in the prompt.

An upload from the chat composer is attached to the Agent Conversation and is
never deleted. Neither end of the app could prove that. The surface had only the
sentence the composer wrote into the message, so a reload left the person who
uploaded the file with no way to open it; and the agent had only that same
sentence, in a turn the context fitter is entitled to drop, so an agent thirty
turns later reproduced a File id one character short and told the user their
attachment was gone.

Both are answered from the record instead. `get_conversation` returns the files,
and the system prompt lists them again at the start of every run — which is what
makes the ids immune to trimming: the section is rebuilt, not remembered.

The permission story does not move. These are the conversation's own files, and
a caller reaches them only through the conversation's own read gate.
"""

from unittest.mock import patch

import frappe

from frappe_agents.api import get_conversation, start_run
from frappe_agents.context.attachments import ATTACHMENT_LIMIT
from frappe_agents.runner.run import ATTACHMENTS_HEADING, build_system_prompt
from frappe_agents.tests.fixtures import (
	AGENT,
	OPEN_USER,
	RESTRICTED_USER,
	AgentTestCase,
	as_user,
	make_conversation,
	make_pdf_attachment,
	make_run,
)

CONVERSATION = "Agent Conversation"
ATTACHMENT_KEYS = {"name", "file_name", "file_url", "is_private", "creation"}


def attach(conversation: str, file_name: str) -> str:
	"""One private PDF on a conversation, the way the composer's uploader leaves it."""
	return make_pdf_attachment(CONVERSATION, conversation, file_name=file_name).name


class TestConversationAttachments(AgentTestCase):
	def read(self, conversation: str, user: str = RESTRICTED_USER, **args) -> dict:
		with as_user(user):
			return get_conversation(conversation, **args)

	def test_the_payload_carries_the_conversations_files(self):
		conversation = make_conversation(RESTRICTED_USER)
		first = attach(conversation.name, "fa-one.pdf")
		second = attach(conversation.name, "fa-two.pdf")

		files = self.read(conversation.name)["attachments"]

		self.assertEqual([file["name"] for file in files], [first, second])
		self.assertEqual([file["file_name"] for file in files], ["fa-one.pdf", "fa-two.pdf"])

	def test_a_file_comes_back_with_what_it_takes_to_open_it(self):
		"""A name to copy, a url to click, and the fact that the url is a private one."""
		conversation = make_conversation(RESTRICTED_USER)
		name = attach(conversation.name, "fa-cv.pdf")

		file = self.read(conversation.name)["attachments"][0]

		self.assertEqual(set(file), ATTACHMENT_KEYS)
		self.assertEqual(file["name"], name)
		self.assertTrue(file["file_url"])
		self.assertTrue(file["is_private"])
		self.assertTrue(file["creation"])

	def test_a_conversation_with_no_files_says_so_with_a_list(self):
		"""Present and empty. A caller that reads the key gets a list, not a KeyError."""
		conversation = make_conversation(RESTRICTED_USER)

		self.assertEqual(self.read(conversation.name)["attachments"], [])

	def test_the_list_is_capped_and_keeps_the_newest(self):
		conversation = make_conversation(RESTRICTED_USER)
		names = [attach(conversation.name, f"fa-{index:03d}.pdf") for index in range(ATTACHMENT_LIMIT + 3)]

		files = self.read(conversation.name)["attachments"]

		self.assertEqual(len(files), ATTACHMENT_LIMIT)
		# The newest are the ones a person came back for, and they are still in
		# the order they arrived.
		self.assertEqual([file["name"] for file in files], names[3:])

	def test_another_users_conversation_is_not_readable_at_all(self):
		"""There is no leak vector here, and this is the test that says why.

		The files ride on the conversation payload, so the only way to ask for them
		is to ask for the conversation — and that door is shut before a single file
		row is read.
		"""
		theirs = make_conversation(OPEN_USER)
		attach(theirs.name, "fa-theirs.pdf")

		with as_user(RESTRICTED_USER), self.assertRaises(frappe.PermissionError):
			get_conversation(theirs.name)

	def test_the_files_come_back_on_every_page_of_a_long_conversation(self):
		"""Paged runs, unpaged files: the strip is the conversation's, not the page's."""
		conversation = make_conversation(RESTRICTED_USER)
		attach(conversation.name, "fa-cv.pdf")
		with as_user(RESTRICTED_USER):
			for turn in range(3):
				make_run(RESTRICTED_USER, conversation=conversation.name, message=f"Turn {turn}")

		page = self.read(conversation.name, limit=1)
		older = self.read(conversation.name, limit=1, before=page["next_before"])

		self.assertTrue(page["truncated"])
		self.assertEqual(
			[file["name"] for file in page["attachments"]],
			[file["name"] for file in older["attachments"]],
		)

	def test_an_older_page_reads_the_turns_above_the_one_before_it(self):
		"""A second page is older than the first, and in the same order within itself."""
		conversation = make_conversation(RESTRICTED_USER)
		with as_user(RESTRICTED_USER):
			for turn in range(1, 5):
				run = make_run(RESTRICTED_USER, conversation=conversation.name, message=f"Turn {turn}")
				frappe.db.set_value(
					"Agent Run",
					run.name,
					{"creation": f"2026-01-01 00:00:0{turn}"},
					update_modified=False,
				)

		page = self.read(conversation.name, limit=2)
		older = self.read(conversation.name, limit=2, before=page["next_before"])

		self.assertEqual([run["input_message"] for run in page["runs"]], ["Turn 3", "Turn 4"])
		# Oldest first inside the page, and every turn in it older than the page
		# it was asked for from above.
		self.assertEqual([run["input_message"] for run in older["runs"]], ["Turn 1", "Turn 2"])
		self.assertLess(str(older["runs"][-1]["creation"]), str(page["runs"][0]["creation"]))


class TestAttachmentPromptSection(AgentTestCase):
	"""The same files, written into the system prompt at the start of every run."""

	def prompt(self, run) -> str:
		return build_system_prompt(frappe.get_doc("Agent", run.agent), run)

	def run_on(self, conversation: str):
		return make_run(RESTRICTED_USER, agent=AGENT, conversation=conversation)

	def test_a_conversation_with_files_lists_every_id_exactly(self):
		conversation = make_conversation(RESTRICTED_USER)
		first = attach(conversation.name, "fa-cv.pdf")
		second = attach(conversation.name, "fa-invoice.pdf")

		prompt = self.prompt(self.run_on(conversation.name))

		self.assertIn(ATTACHMENTS_HEADING, prompt)
		self.assertIn(f"- fa-cv.pdf (File: {first})", prompt)
		self.assertIn(f"- fa-invoice.pdf (File: {second})", prompt)

	def test_a_conversation_with_no_files_gets_no_section(self):
		conversation = make_conversation(RESTRICTED_USER)

		self.assertNotIn(ATTACHMENTS_HEADING, self.prompt(self.run_on(conversation.name)))

	def test_a_run_outside_any_conversation_gets_no_section(self):
		self.assertNotIn(ATTACHMENTS_HEADING, self.prompt(make_run(RESTRICTED_USER, agent=AGENT)))

	def test_the_section_is_capped_and_says_how_many_it_left_out(self):
		conversation = make_conversation(RESTRICTED_USER)
		extra = 3
		names = [
			attach(conversation.name, f"fa-{index:03d}.pdf") for index in range(ATTACHMENT_LIMIT + extra)
		]

		prompt = self.prompt(self.run_on(conversation.name))

		self.assertEqual(prompt.count("(File: "), ATTACHMENT_LIMIT)
		self.assertIn(f"- and {extra} older files, not listed here.", prompt)
		# Capped from the old end: the newest file is named, the oldest is not.
		self.assertIn(names[-1], prompt)
		self.assertNotIn(names[0], prompt)

	def test_the_section_survives_a_history_the_fitter_would_drop(self):
		"""The point of the whole thing: the ids are rebuilt, never remembered.

		The prompt is composed from the record at the start of the run, so a
		transcript trimmed down to nothing still leaves every File id in front of
		the model.
		"""
		conversation = make_conversation(RESTRICTED_USER)
		name = attach(conversation.name, "fa-cv.pdf")
		with as_user(RESTRICTED_USER), patch("frappe.enqueue"):
			start_run(agent=AGENT, message="Read this.", conversation=conversation.name)

		later = self.run_on(conversation.name)

		self.assertIn(f"(File: {name})", self.prompt(later))
