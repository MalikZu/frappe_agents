# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Accepting and discarding: who may, when, and what it writes.

The review surface is the Document Extraction form, so these endpoints run with
the reviewer's own session and their own permissions carry every write. Extraction
adds no new submit path and no new way to write a document: the draft is saved by
the person reviewing it, validated in full, and the extraction row records who
that was.
"""

import frappe

from frappe_agents.api import apply_extraction, discard_extraction
from frappe_agents.tests.fixtures import (
	DRAFT_USER,
	ORDER_DT,
	PROJECT_ALPHA,
	SECOND_DRAFTER,
	SKILL_WRITER,
	VENDOR_ACME,
	AgentTestCase,
	as_user,
	extract_as,
	extraction_json,
	extraction_reply,
	make_pdf_attachment,
)

NEEDS_REVIEW = "Needs Review"
ACCEPTED = "Accepted"
DISCARDED = "Discarded"


class TestExtractionReview(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.file = make_pdf_attachment()

	def extract(self, **extra):
		values = {
			"order_title": f"FA Extracted {frappe.generate_hash(length=8)}",
			"project": PROJECT_ALPHA,
			"vendor": VENDOR_ACME,
			"amount": 130,
		}
		values.update(extra)
		return extract_as(DRAFT_USER, self.file.name, extraction_reply(values))[0]

	def values(self, doc) -> dict:
		return dict(extraction_json(doc, "extracted_json"))

	# --- who may review ------------------------------------------------------

	def test_another_user_may_not_accept_someone_elses_extraction(self):
		doc = self.extract()

		with as_user(SECOND_DRAFTER), self.assertRaises(frappe.PermissionError) as caught:
			apply_extraction(doc.name, values=self.values(doc), confirmed=[])

		self.assertIn(DRAFT_USER, str(caught.exception))
		self.assertEqual(frappe.db.get_value(doc.doctype, doc.name, "status"), NEEDS_REVIEW)

	def test_a_system_manager_may_accept_it(self):
		doc = self.extract()

		with as_user(SKILL_WRITER):
			result = apply_extraction(doc.name, values=self.values(doc), confirmed=[])

		self.assertEqual(result["status"], ACCEPTED)
		self.assertEqual(frappe.db.get_value(doc.doctype, doc.name, "reviewed_by"), SKILL_WRITER)

	def test_another_user_may_not_discard_it_either(self):
		doc = self.extract()

		with as_user(SECOND_DRAFTER), self.assertRaises(frappe.PermissionError):
			discard_extraction(doc.name)

	# --- what accepting writes -----------------------------------------------

	def test_the_reviewers_edits_are_what_reaches_the_draft(self):
		doc = self.extract()
		values = self.values(doc)
		values["amount"] = 175
		values["notes"] = "Checked against the signed quotation."

		with as_user(DRAFT_USER):
			result = apply_extraction(doc.name, values=values, confirmed=[])

		self.assertIn("amount", result["applied_fields"])
		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertEqual(draft.amount, 175)
		self.assertEqual(draft.notes, "Checked against the signed quotation.")

	def test_a_key_that_is_not_a_field_we_asked_for_is_named_and_ignored(self):
		doc = self.extract()
		values = self.values(doc)
		values.update({"docstatus": 1, "owner": "Administrator", "__islocal": 1, "made_up": "x"})

		with as_user(DRAFT_USER):
			result = apply_extraction(doc.name, values=values, confirmed=[])

		self.assertEqual(sorted(result["ignored_keys"]), ["__islocal", "docstatus", "made_up", "owner"])
		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertEqual(draft.docstatus, 0)
		self.assertEqual(draft.owner, DRAFT_USER)

	def test_accepting_records_the_reviewer_and_the_values_that_were_applied(self):
		doc = self.extract()

		with as_user(DRAFT_USER):
			apply_extraction(doc.name, values=self.values(doc), confirmed=[])

		reloaded = frappe.get_doc(doc.doctype, doc.name)
		self.assertEqual(reloaded.status, ACCEPTED)
		self.assertEqual(reloaded.reviewed_by, DRAFT_USER)
		self.assertEqual(extraction_json(reloaded, "extracted_json")["amount"], 130)

	def test_the_draft_is_validated_in_full_when_it_is_accepted(self):
		"""The holes left for review have to be filled before the document is accepted."""
		doc = self.extract()
		values = self.values(doc)
		values["order_title"] = ""

		with as_user(DRAFT_USER), self.assertRaises(frappe.ValidationError):
			apply_extraction(doc.name, values=values, confirmed=[])

		self.assertEqual(frappe.db.get_value(doc.doctype, doc.name, "status"), NEEDS_REVIEW)

	# --- state ---------------------------------------------------------------

	def test_an_extraction_with_no_draft_cannot_be_accepted(self):
		doc = self.extract()
		doc.db_set("created_doc", None, update_modified=False)

		with as_user(DRAFT_USER), self.assertRaises(frappe.ValidationError) as caught:
			apply_extraction(doc.name, values={}, confirmed=[])

		self.assertIn("no draft", str(caught.exception))

	def test_an_extraction_that_is_not_waiting_for_review_cannot_be_accepted(self):
		doc = self.extract()
		with as_user(DRAFT_USER):
			apply_extraction(doc.name, values=self.values(doc), confirmed=[])

		with as_user(DRAFT_USER), self.assertRaises(frappe.ValidationError) as caught:
			apply_extraction(doc.name, values=self.values(doc), confirmed=[])

		self.assertIn(ACCEPTED, str(caught.exception))

	def test_discarding_is_terminal(self):
		doc = self.extract()

		with as_user(DRAFT_USER):
			result = discard_extraction(doc.name)
		self.assertEqual(result["status"], DISCARDED)

		with as_user(DRAFT_USER), self.assertRaises(frappe.ValidationError):
			discard_extraction(doc.name)

		with as_user(DRAFT_USER), self.assertRaises(frappe.ValidationError):
			apply_extraction(doc.name, values=self.values(doc), confirmed=[])

	def test_discarding_leaves_the_draft_where_it_is(self):
		"""Deleting a document is a decision for a person, not a side effect of closing a review."""
		doc = self.extract()

		with as_user(DRAFT_USER):
			result = discard_extraction(doc.name)

		self.assertTrue(frappe.db.exists(ORDER_DT, doc.created_doc))
		self.assertEqual(result["created_doc"], doc.created_doc)
		self.assertIn("left alone", result["note"])

	def test_a_draft_that_is_no_longer_a_draft_is_not_written_to(self):
		doc = self.extract()
		frappe.db.set_value(ORDER_DT, doc.created_doc, "docstatus", 1, update_modified=False)
		frappe.clear_document_cache(ORDER_DT, doc.created_doc)

		with as_user(DRAFT_USER), self.assertRaises(frappe.ValidationError) as caught:
			apply_extraction(doc.name, values=self.values(doc), confirmed=[])

		self.assertIn("no longer a draft", str(caught.exception))
