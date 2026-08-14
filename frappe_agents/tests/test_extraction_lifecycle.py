# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The extraction row is a state machine, and a person's decision is the last word.

An extraction is a request on one side and a job on the other, and the job is slow:
a model call sits in the middle of it. Everything here is about that gap. Somebody
can discard the reading while the model is still writing, and when the job comes
back it must not undo what they decided — no draft inserted, no `Needs Review` over
their `Discarded`, no failure written on a row they already closed.

Discarding a Running row is allowed rather than refused: cancelling a reading you
no longer want is the whole point of the button, and telling a user to wait for a
worker they cannot see would make the job's timing their problem. What makes it
safe is on the worker's side — every status write re-reads the row it is about to
move, so a discard lands whether or not the model has answered yet.

The model is mocked as a function that runs the second request before it answers.
That is what the interleaving actually looks like: the discard happens inside the
window the model call holds open.
"""

from unittest.mock import patch

import frappe

from frappe_agents.api import discard_extraction
from frappe_agents.extraction.pipeline import EXTRACTION, _create_draft
from frappe_agents.runner.providers import ProviderError
from frappe_agents.tests.fixtures import (
	DRAFT_USER,
	ORDER_DT,
	PROJECT_ALPHA,
	VENDOR_ACME,
	AgentTestCase,
	as_user,
	extraction_reply,
	make_pdf_attachment,
	queue_extraction_as,
	run_extraction_with,
)

DISCARDED = "Discarded"
NEEDS_REVIEW = "Needs Review"


class TestExtractionLifecycle(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.file = make_pdf_attachment()
		self.drafts_before = frappe.db.count(ORDER_DT)

	def fields(self) -> dict:
		return {
			"order_title": f"FA Extracted {frappe.generate_hash(length=8)}",
			"project": PROJECT_ALPHA,
			"vendor": VENDOR_ACME,
			"amount": 120,
		}

	def discard(self) -> None:
		"""The second request: the person closing the reading, as themselves."""
		with as_user(DRAFT_USER):
			discard_extraction(self.extraction)

	def assert_no_draft_was_created(self) -> None:
		self.assertEqual(frappe.db.count(ORDER_DT), self.drafts_before)

	def queue(self) -> str:
		self.extraction = queue_extraction_as(DRAFT_USER, self.file.name)
		return self.extraction

	def test_discard_while_extraction_worker_is_running_is_terminal(self):
		"""The finding this file exists for: a decision the worker cannot undo.

		The row is Running, the model is mid-answer, and the person discards it. The
		answer arrives, and the job drops it: nothing is inserted, the status stays
		Discarded, and the row says why it holds no reading.
		"""
		self.queue()

		def discard_then_answer(*args, **kwargs):
			self.discard()
			return extraction_reply(self.fields())

		doc, call = run_extraction_with(self.extraction, side_effect=discard_then_answer)

		self.assertEqual(call.call_count, 1)
		self.assertEqual(doc.status, DISCARDED)
		self.assertFalse(doc.created_doc)
		self.assertEqual(doc.reviewed_by, DRAFT_USER)
		self.assertIn("dropped", doc.error)
		self.assert_no_draft_was_created()

	def test_a_row_discarded_before_the_worker_starts_is_never_extracted(self):
		"""The other half of the same window: discarded between the queue and the job.

		The model is never called at all — the claim on the row is what starts the
		work, and there is nothing to claim.
		"""
		self.queue()
		self.discard()

		doc, call = run_extraction_with(self.extraction, extraction_reply(self.fields()))

		call.assert_not_called()
		self.assertEqual(doc.status, DISCARDED)
		self.assert_no_draft_was_created()

	def test_a_discarded_row_is_not_reopened_by_a_late_failure(self):
		"""A failure is the job's result too, and it does not outrank a decision."""
		self.queue()

		def discard_then_fail(*args, **kwargs):
			self.discard()
			raise ProviderError("Model request to http://localhost:1/v1 failed.")

		doc, _ = run_extraction_with(self.extraction, side_effect=discard_then_fail)

		self.assertEqual(doc.status, DISCARDED)
		self.assertIn("dropped", doc.error)
		self.assert_no_draft_was_created()

	def test_a_draft_inserted_in_the_width_of_a_discard_is_named_on_the_row(self):
		"""The narrowest window there is: the discard lands during the insert itself.

		The document exists by then, and deleting somebody's record is not something
		a background job decides. So the row keeps its Discarded status and carries
		the name of the draft, because a document nothing points at is worse than one
		a reviewer can find and delete.
		"""
		self.queue()

		def create_then_discard(target_doctype: str, values: dict) -> str:
			created = _create_draft(target_doctype, values)
			self.discard()
			return created

		with patch("frappe_agents.extraction.pipeline._create_draft", side_effect=create_then_discard):
			doc, _ = run_extraction_with(self.extraction, extraction_reply(self.fields()))

		self.assertEqual(doc.status, DISCARDED)
		self.assertTrue(doc.created_doc)
		self.assertIn(doc.created_doc, doc.error)
		self.assertEqual(frappe.db.get_value(ORDER_DT, doc.created_doc, "docstatus"), 0)

	def test_a_reading_nobody_interrupted_still_ends_in_needs_review(self):
		"""The positive control. The guards only fire on a row that moved."""
		self.queue()

		doc, call = run_extraction_with(self.extraction, extraction_reply(self.fields()))

		self.assertEqual(call.call_count, 1)
		self.assertEqual(doc.status, NEEDS_REVIEW)
		self.assertTrue(doc.created_doc)
		self.assertFalse(doc.error)
		self.assertEqual(frappe.db.count(ORDER_DT), self.drafts_before + 1)

	def test_discarding_twice_is_refused_rather_than_rewritten(self):
		"""Idempotence from the request side: the second discard changes nothing."""
		self.queue()
		self.discard()
		decided_at = frappe.db.get_value(EXTRACTION, self.extraction, "modified")

		with as_user(DRAFT_USER), self.assertRaises(frappe.ValidationError):
			discard_extraction(self.extraction)

		self.assertEqual(frappe.db.get_value(EXTRACTION, self.extraction, "status"), DISCARDED)
		self.assertEqual(frappe.db.get_value(EXTRACTION, self.extraction, "modified"), decided_at)
