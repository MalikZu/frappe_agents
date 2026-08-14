# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Files uploaded from the chat composer, and the record they hang off.

A file handed to an agent has to be attached to something a reviewer can open —
that is the extraction anchor rule, and it does not move here. What moves is that
a chat now has a record to offer: the conversation itself. It says who supplied
the file, to which agent, and what was being talked about around it.

So the questions are: can a conversation be opened before anything is said, is
the file that lands on it readable by its owner and nobody else, and does
extraction accept it the way it accepts a file on any other record.
"""

from typing import Any
from unittest.mock import patch

import frappe

from frappe_agents.api import start_conversation, start_run
from frappe_agents.tests.fixtures import (
	AGENT,
	DRAFT_AGENT,
	DRAFT_USER,
	ORDER_DT,
	PROFILE,
	RESTRICTED_USER,
	SECOND_DRAFTER,
	AgentTestCase,
	as_user,
	call_tool,
	make_pdf,
	set_kill_switch,
)

CONVERSATION = "Agent Conversation"


def attach_pdf(conversation: str, user: str, file_name: str = "fa-cv.pdf") -> Any:
	"""Upload one private PDF onto a conversation, as the person in the chat.

	Inserted through that user's own permissions, exactly as frappe's uploader
	does it: the write check on the conversation is the whole gate.
	"""
	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"attached_to_doctype": CONVERSATION,
			"attached_to_name": conversation,
			"is_private": 1,
			"content": make_pdf(file_name),
		}
	)
	with as_user(user):
		file.insert()
	return file


class TestChatUploads(AgentTestCase):
	def test_a_conversation_can_be_opened_before_anything_is_said(self):
		with as_user(RESTRICTED_USER):
			result = start_conversation(agent=AGENT)

		values = frappe.db.get_value(
			CONVERSATION, result["conversation"], ["user", "owner", "agent", "title"], as_dict=True
		)
		self.assertEqual(values.user, RESTRICTED_USER)
		self.assertEqual(values.owner, RESTRICTED_USER)
		self.assertEqual(values.agent, AGENT)
		# Nothing has been said, so there is nothing to call it and nothing queued.
		self.assertFalse(values.title)
		self.assertFalse(frappe.db.exists("Agent Run", {"conversation": result["conversation"]}))

	def test_the_first_message_carries_on_the_conversation_the_upload_opened(self):
		with as_user(RESTRICTED_USER):
			opened = start_conversation(agent=AGENT)
			with patch("frappe.enqueue"):
				run = start_run(
					agent=AGENT,
					message="Read this.\n\nAttached files: fa-cv.pdf (File: abc123)",
					conversation=opened["conversation"],
				)

		self.assertEqual(run["conversation"], opened["conversation"])
		self.assertEqual(
			frappe.db.count(CONVERSATION, {"user": RESTRICTED_USER, "agent": AGENT}),
			1,
		)

	def test_a_switched_off_runtime_opens_no_conversation(self):
		set_kill_switch(0)
		self.addCleanup(set_kill_switch, 1)

		with as_user(RESTRICTED_USER), self.assertRaises(frappe.ValidationError):
			start_conversation(agent=AGENT)

		self.assertFalse(frappe.db.exists(CONVERSATION, {"user": RESTRICTED_USER, "agent": AGENT}))

	def test_an_agent_this_user_may_not_talk_to_opens_no_conversation(self):
		"""The same refusal `start_run` makes, at the same point: before any write."""
		agent = frappe.get_doc(
			{
				"doctype": "Agent",
				"agent_name": f"FA Gated Agent {frappe.generate_hash(length=6)}",
				"enabled": 1,
				"run_as": "Session User",
				"model_profile": PROFILE,
				"autonomy": "Suggest",
				"max_steps": 3,
				"allowed_roles": [{"role": "System Manager"}],
			}
		)
		agent.flags.ignore_permissions = True
		agent.insert(ignore_permissions=True)

		with as_user(RESTRICTED_USER), self.assertRaises(frappe.PermissionError):
			start_conversation(agent=agent.name)

		self.assertFalse(frappe.db.exists(CONVERSATION, {"agent": agent.name}))

	def test_a_file_uploaded_to_a_conversation_is_readable_by_the_person_in_it(self):
		with as_user(DRAFT_USER):
			conversation = start_conversation(agent=DRAFT_AGENT)["conversation"]
		file = attach_pdf(conversation, DRAFT_USER)

		with as_user(DRAFT_USER):
			self.assertTrue(frappe.has_permission("File", "read", doc=file.name))

	def test_nobody_else_reads_it_through_a_conversation_they_cannot_read(self):
		"""A conversation is one user's. A file on it is reachable by that user."""
		with as_user(DRAFT_USER):
			conversation = start_conversation(agent=DRAFT_AGENT)["conversation"]
		file = attach_pdf(conversation, DRAFT_USER)

		with as_user(SECOND_DRAFTER):
			self.assertFalse(frappe.has_permission(CONVERSATION, "read", doc=conversation))
			self.assertFalse(frappe.has_permission("File", "read", doc=file.name))

	def test_extraction_accepts_a_file_anchored_to_the_conversation_it_arrived_in(self):
		"""The anchor rule is unchanged: a conversation is a record like any other."""
		with as_user(DRAFT_USER):
			conversation = start_conversation(agent=DRAFT_AGENT)["conversation"]
		file = attach_pdf(conversation, DRAFT_USER)

		with patch("frappe.enqueue"):
			payload, _ = call_tool(
				DRAFT_USER,
				"extract_document",
				{"file": file.name, "target_doctype": ORDER_DT},
				agent=DRAFT_AGENT,
			)

		self.assertTrue(payload["ok"], payload["error"])
		extraction = payload["result"]["extraction"]
		self.assertEqual(frappe.db.get_value("Document Extraction", extraction, "source_file"), file.name)

	def test_extraction_refuses_a_file_on_a_conversation_that_is_not_this_users(self):
		"""Provenance is a read check on the anchor, and the anchor is a conversation."""
		with as_user(DRAFT_USER):
			conversation = start_conversation(agent=DRAFT_AGENT)["conversation"]
		file = attach_pdf(conversation, DRAFT_USER)

		with patch("frappe.enqueue"):
			payload, _ = call_tool(
				SECOND_DRAFTER,
				"extract_document",
				{"file": file.name, "target_doctype": ORDER_DT},
				agent=DRAFT_AGENT,
			)

		self.assertFalse(payload["ok"])
		self.assertFalse(frappe.db.exists("Document Extraction", {"source_file": file.name}))
