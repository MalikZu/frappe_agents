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

A chat opened on a document is the other half. There the document is the anchor,
because it is the better one: it is what the file is about, and it is already
readable by everyone who reviews that record rather than by one person.
"""

from typing import Any
from unittest.mock import patch

import frappe
from frappe.handler import check_write_permission

from frappe_agents.api import start_conversation, start_run
from frappe_agents.tests.fixtures import (
	AGENT,
	DRAFT_AGENT,
	DRAFT_USER,
	ORDER_DT,
	PROFILE,
	RESTRICTED_USER,
	SECOND_DRAFTER,
	VAULT_DT,
	VAULT_RECORD,
	AgentTestCase,
	as_user,
	call_tool,
	extraction_reply,
	make_order_draft,
	make_pdf,
	run_extraction_with,
	set_kill_switch,
)

CONVERSATION = "Agent Conversation"


def upload_as(user: str, doctype: str, name: str, file_name: str = "fa-cv.pdf") -> Any:
	"""Upload one private PDF onto a record, the way the composer's uploader does.

	`check_write_permission` is the gate frappe's upload endpoint puts in front of
	every attachment, so the helper goes through it first: what a chat may anchor a
	file to is exactly what the person in it may write. The insert then runs as that
	user, with no permissions ignored anywhere.
	"""
	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"attached_to_doctype": doctype,
			"attached_to_name": name,
			"is_private": 1,
			"content": make_pdf(f"{doctype} {name} {file_name}"),
		}
	)
	with as_user(user):
		check_write_permission(doctype, name)
		file.insert()
	return file


def attach_pdf(conversation: str, user: str, file_name: str = "fa-cv.pdf") -> Any:
	"""The same upload, onto a conversation — what the composer does in a plain chat."""
	return upload_as(user, CONVERSATION, conversation, file_name)


def extract(user: str, file_name: str) -> dict:
	"""Ask for one extraction the way the agent does, without queueing the job."""
	with patch("frappe.enqueue"):
		payload, _ = call_tool(
			user,
			"extract_document",
			{"file": file_name, "target_doctype": ORDER_DT},
			agent=DRAFT_AGENT,
		)
	return payload


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

	def test_a_file_uploaded_in_chat_lands_on_the_conversation_it_was_sent_in(self):
		"""The upload is the anchor: a private File hanging off this conversation."""
		with as_user(DRAFT_USER):
			conversation = start_conversation(agent=DRAFT_AGENT)["conversation"]

		file = attach_pdf(conversation, DRAFT_USER)

		stored = frappe.db.get_value(
			"File",
			file.name,
			["attached_to_doctype", "attached_to_name", "is_private", "owner", "is_folder"],
			as_dict=True,
		)
		self.assertEqual(stored.attached_to_doctype, CONVERSATION)
		self.assertEqual(stored.attached_to_name, conversation)
		self.assertTrue(stored.is_private)
		self.assertFalse(stored.is_folder)
		self.assertEqual(stored.owner, DRAFT_USER)

	def test_a_conversation_that_is_not_yours_is_nothing_to_upload_to(self):
		"""The anchor is a record with an owner, so attaching to it is a write."""
		with as_user(DRAFT_USER):
			conversation = start_conversation(agent=DRAFT_AGENT)["conversation"]

		with as_user(SECOND_DRAFTER), self.assertRaises(frappe.PermissionError):
			check_write_permission(CONVERSATION, conversation)

		self.assertFalse(
			frappe.db.exists("File", {"attached_to_doctype": CONVERSATION, "owner": SECOND_DRAFTER})
		)

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

		payload = extract(DRAFT_USER, file.name)

		self.assertTrue(payload["ok"], payload["error"])
		extraction = payload["result"]["extraction"]
		self.assertEqual(frappe.db.get_value("Document Extraction", extraction, "source_file"), file.name)

	def test_a_file_uploaded_in_chat_is_read_all_the_way_into_a_draft(self):
		"""The whole way through, with nothing mocked but the call to the model.

		Upload in a chat, ask the agent to read it, run the job: a draft comes out
		for a person to review, and it was anchored to nothing but the conversation.
		"""
		with as_user(DRAFT_USER):
			conversation = start_conversation(agent=DRAFT_AGENT)["conversation"]
		file = attach_pdf(conversation, DRAFT_USER)

		payload = extract(DRAFT_USER, file.name)
		self.assertTrue(payload["ok"], payload["error"])

		doc, call = run_extraction_with(
			payload["result"]["extraction"],
			extraction_reply({"order_title": "FA Chat Upload"}),
		)

		self.assertTrue(call.called)
		self.assertEqual(doc.status, "Needs Review")
		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertEqual(draft.order_title, "FA Chat Upload")
		self.assertEqual(draft.docstatus, 0)
		self.assertEqual(draft.owner, DRAFT_USER)

	def test_extraction_refuses_a_file_on_a_conversation_that_is_not_this_users(self):
		"""Provenance is a read check on the anchor, and the anchor is a conversation."""
		with as_user(DRAFT_USER):
			conversation = start_conversation(agent=DRAFT_AGENT)["conversation"]
		file = attach_pdf(conversation, DRAFT_USER)

		payload = extract(SECOND_DRAFTER, file.name)

		self.assertFalse(payload["ok"])
		self.assertFalse(frappe.db.exists("Document Extraction", {"source_file": file.name}))


class TestFormPanelUploads(AgentTestCase):
	"""A chat opened on a document anchors what is uploaded to that document.

	The composer picks the anchor, and when there is an open document it picks that
	one over the conversation. These say what the choice is worth: the file lands on
	the record it is about, extraction takes it, and it is readable by everyone who
	reviews that record — which a conversation, being one person's, never is.
	"""

	def setUp(self) -> None:
		super().setUp()
		self.order = make_order_draft()

	def test_an_upload_from_a_form_panel_lands_on_the_open_document(self):
		file = upload_as(DRAFT_USER, ORDER_DT, self.order.name)

		stored = frappe.db.get_value(
			"File", file.name, ["attached_to_doctype", "attached_to_name", "is_private"], as_dict=True
		)
		self.assertEqual(stored.attached_to_doctype, ORDER_DT)
		self.assertEqual(stored.attached_to_name, self.order.name)
		self.assertTrue(stored.is_private)
		# The document held the file, so no conversation had to be opened to hold it.
		self.assertFalse(frappe.db.exists(CONVERSATION, {"user": DRAFT_USER, "agent": DRAFT_AGENT}))

	def test_a_document_this_user_may_not_write_is_not_an_anchor(self):
		"""Nobody in the cast may touch the vault, so no chat may put a file on it."""
		with as_user(DRAFT_USER), self.assertRaises(frappe.PermissionError):
			check_write_permission(VAULT_DT, VAULT_RECORD)

	def test_the_document_anchor_is_shared_with_everyone_who_reads_the_document(self):
		"""Why the open document is the better anchor: the file inherits its readers."""
		file = upload_as(DRAFT_USER, ORDER_DT, self.order.name)

		with as_user(SECOND_DRAFTER):
			self.assertTrue(frappe.has_permission(ORDER_DT, "read", doc=self.order.name))
			self.assertTrue(frappe.has_permission("File", "read", doc=file.name))

	def test_a_file_uploaded_on_a_document_is_read_all_the_way_into_a_draft(self):
		file = upload_as(DRAFT_USER, ORDER_DT, self.order.name)

		payload = extract(DRAFT_USER, file.name)
		self.assertTrue(payload["ok"], payload["error"])

		doc, _ = run_extraction_with(
			payload["result"]["extraction"],
			extraction_reply({"order_title": "FA Form Panel Upload"}),
		)

		self.assertEqual(doc.status, "Needs Review")
		self.assertEqual(doc.source_file, file.name)
		self.assertEqual(frappe.get_value(ORDER_DT, doc.created_doc, "order_title"), "FA Form Panel Upload")
