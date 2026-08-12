# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Opening a run on a document.

A run seeded with a document is a run that will read that document under the
user's own identity, so the permission check happens at the door — before a
conversation, before a run row, before anything is queued. A refusal has to
leave nothing behind.
"""

from unittest.mock import patch

import frappe

from frappe_agents.api import start_run
from frappe_agents.tests.fixtures import (
	AGENT,
	ORDER_DT,
	ORDER_LIVE,
	RESTRICTED_USER,
	TICKET_ALPHA,
	TICKET_BETA,
	TICKET_DT,
	VAULT_DT,
	VAULT_RECORD,
	AgentTestCase,
	as_user,
)


class TestContextSeeding(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.runs = frappe.db.count("Agent Run")
		self.conversations = frappe.db.count("Agent Conversation")

	def assert_nothing_was_written(self) -> None:
		self.assertEqual(frappe.db.count("Agent Run"), self.runs)
		self.assertEqual(frappe.db.count("Agent Conversation"), self.conversations)

	def refuse(self, exception, **context):
		with as_user(RESTRICTED_USER), patch("frappe.enqueue") as enqueue:
			with self.assertRaises(exception):
				start_run(agent=AGENT, message="What does this say?", **context)
			enqueue.assert_not_called()
		self.assert_nothing_was_written()

	def test_a_doctype_the_user_cannot_read_is_refused_at_the_door(self):
		self.refuse(frappe.PermissionError, context_doctype=VAULT_DT, context_name=VAULT_RECORD)

	def test_a_row_the_user_cannot_read_is_refused_at_the_door(self):
		"""The document is readable to others; a User Permission puts it out of reach here."""
		self.refuse(frappe.PermissionError, context_doctype=TICKET_DT, context_name=TICKET_BETA)

	def test_half_a_context_is_refused(self):
		self.refuse(frappe.ValidationError, context_doctype=TICKET_DT)
		self.refuse(frappe.ValidationError, context_name=TICKET_ALPHA)

	def test_a_document_that_does_not_exist_is_refused(self):
		self.refuse(frappe.ValidationError, context_doctype=TICKET_DT, context_name="FA Ticket Nowhere")
		self.refuse(frappe.ValidationError, context_doctype="FA No Such DocType", context_name="x")

	def test_a_readable_document_is_stamped_on_the_run(self):
		with as_user(RESTRICTED_USER), patch("frappe.enqueue") as enqueue:
			result = start_run(
				agent=AGENT,
				message="Summarise this ticket.",
				context_doctype=TICKET_DT,
				context_name=TICKET_ALPHA,
			)
			enqueue.assert_called_once()

		values = frappe.db.get_value(
			"Agent Run",
			result["run"],
			["context_doctype", "context_name", "effective_user"],
			as_dict=True,
		)
		self.assertEqual(values.context_doctype, TICKET_DT)
		self.assertEqual(values.context_name, TICKET_ALPHA)
		self.assertEqual(values.effective_user, RESTRICTED_USER)

	def test_a_run_without_a_document_carries_no_context(self):
		with as_user(RESTRICTED_USER), patch("frappe.enqueue"):
			result = start_run(agent=AGENT, message="How many orders are open?")

		values = frappe.db.get_value(
			"Agent Run", result["run"], ["context_doctype", "context_name"], as_dict=True
		)
		self.assertFalse(values.context_doctype)
		self.assertFalse(values.context_name)

	def test_a_submitted_document_is_accepted(self):
		"""The gate is permission, not docstatus: a submitted order is a fine thing to ask about."""
		with as_user(RESTRICTED_USER), patch("frappe.enqueue"):
			result = start_run(
				agent=AGENT,
				message="What is on this order?",
				context_doctype=ORDER_DT,
				context_name=ORDER_LIVE,
			)

		self.assertEqual(frappe.db.get_value("Agent Run", result["run"], "context_name"), ORDER_LIVE)
