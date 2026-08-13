# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The document a conversation is about, after a reload.

A conversation opened from a form knows which document it is about, and every
run in it carries that document. The chat surface showed a chip for it while the
conversation was live and lost the chip on reload, because nothing handed the
context back. This is what hands it back.

The permission is asked again on every read rather than remembered from the run:
the run proves the user could read the document once, which is not the question.
When the answer is no, what comes back names nothing at all.
"""

from unittest.mock import patch

import frappe

from frappe_agents.api import get_conversation, start_run
from frappe_agents.tests.fixtures import (
	AGENT,
	AUDITOR_USER,
	DRAFT_USER,
	ORDER_DT,
	RESTRICTED_USER,
	TICKET_ALPHA,
	TICKET_DT,
	AgentTestCase,
	as_user,
	make_order_draft,
)


class TestConversationContext(AgentTestCase):
	def seed(self, user: str = RESTRICTED_USER, **context) -> dict:
		with as_user(user), patch("frappe.enqueue"):
			return start_run(agent=AGENT, message="What does this say?", **context)

	def read(self, conversation: str, user: str = RESTRICTED_USER) -> dict:
		with as_user(user):
			return get_conversation(conversation)

	def test_the_document_comes_back_on_reload(self):
		started = self.seed(context_doctype=TICKET_DT, context_name=TICKET_ALPHA)

		context = self.read(started["conversation"])["context"]
		self.assertEqual(context["restricted"], 0)
		self.assertEqual(context["doctype"], TICKET_DT)
		self.assertEqual(context["name"], TICKET_ALPHA)
		self.assertEqual(context["title"], TICKET_ALPHA)

	def test_a_conversation_about_nothing_has_no_context(self):
		started = self.seed()

		self.assertIsNone(self.read(started["conversation"])["context"])

	def test_the_first_document_is_the_one_the_conversation_is_about(self):
		started = self.seed(context_doctype=TICKET_DT, context_name=TICKET_ALPHA)
		with as_user(RESTRICTED_USER), patch("frappe.enqueue"):
			start_run(agent=AGENT, message="And now in general?", conversation=started["conversation"])

		context = self.read(started["conversation"])["context"]
		self.assertEqual(context["name"], TICKET_ALPHA)

	def test_a_reader_who_lost_access_is_told_nothing_about_it(self):
		"""The auditor may read the conversation and may not read the ticket."""
		started = self.seed(context_doctype=TICKET_DT, context_name=TICKET_ALPHA)

		payload = self.read(started["conversation"], user=AUDITOR_USER)

		self.assertEqual(payload["context"], {"restricted": 1})
		# Not the name, not the doctype, not the title — anywhere in the answer.
		self.assertNotIn(TICKET_ALPHA, frappe.as_json(payload["context"]))

	def test_a_document_that_is_gone_leaves_no_chip(self):
		order = make_order_draft(user=DRAFT_USER)
		started = self.seed(user=DRAFT_USER, context_doctype=ORDER_DT, context_name=order.name)

		frappe.delete_doc(ORDER_DT, order.name, force=True, ignore_permissions=True)

		self.assertIsNone(self.read(started["conversation"], user=DRAFT_USER)["context"])
