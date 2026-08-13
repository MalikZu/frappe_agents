# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Link resolution: propose a master record, never create one.

A document says "FA Acme Trading". A Link field wants a record name. Between those
two facts sits the thing extraction must not do, which is decide — and the thing
it must never do, which is create. A document from outside cannot add a master
record to the site, and that is not a setting anyone can turn on.

The rest is arithmetic on candidates: one permitted match fills the field, zero or
many leave it empty and ship the text to the reviewer, and a doctype the user may
not read produces neither a value nor a list of names they have not earned.
"""

import frappe

from frappe_agents.tests.fixtures import (
	BLIND_DRAFTER,
	DRAFT_USER,
	ORDER_DT,
	PROJECT_ALPHA,
	VENDOR_ACME,
	VENDOR_ACME_HOLDINGS,
	VENDOR_DT,
	AgentTestCase,
	extract_as,
	extraction_json,
	extraction_reply,
	make_pdf_attachment,
)


class TestExtractionResolve(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.file = make_pdf_attachment()
		self.vendors_before = frappe.db.count(VENDOR_DT)

	def extract(self, user: str = DRAFT_USER, **extra):
		values = {
			"order_title": f"FA Extracted {frappe.generate_hash(length=8)}",
			"amount": 90,
		}
		values.update(extra)
		return extract_as(user, self.file.name, extraction_reply(values))

	def candidates(self, doc) -> dict:
		return extraction_json(doc, "link_candidates")

	def test_a_single_permitted_match_fills_the_field_in(self):
		doc, _ = self.extract(vendor=VENDOR_ACME, project=PROJECT_ALPHA)

		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertEqual(draft.vendor, VENDOR_ACME)
		self.assertEqual(draft.project, PROJECT_ALPHA)
		self.assertEqual(self.candidates(doc), {})

	def test_an_ambiguous_name_leaves_the_field_empty_and_ships_the_candidates(self):
		"""Two records could be this vendor. Picking one is the reviewer's job."""
		doc, _ = self.extract(vendor="FA Acme Trading H")

		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertFalse(draft.vendor)

		candidate = self.candidates(doc)["vendor"]
		self.assertEqual(candidate["doctype"], VENDOR_DT)
		self.assertEqual(candidate["text"], "FA Acme Trading H")
		self.assertIn(VENDOR_ACME_HOLDINGS, [option["name"] for option in candidate["options"]])

	def test_a_name_that_matches_nothing_is_never_written_through_as_text(self):
		"""An unresolved link written as raw text fails validation and takes the draft with it."""
		doc, _ = self.extract(vendor="FA Nobody Ltd")

		self.assertEqual(doc.status, "Needs Review")
		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertFalse(draft.vendor)

		candidate = self.candidates(doc)["vendor"]
		self.assertEqual(candidate["options"], [])
		self.assertIn("No FA Test Vendor matched", candidate["note"])

	def test_resolution_never_inserts_a_master_record(self):
		self.extract(vendor="FA Brand New Vendor LLC")

		self.assertEqual(frappe.db.count(VENDOR_DT), self.vendors_before)
		self.assertFalse(frappe.db.exists(VENDOR_DT, "FA Brand New Vendor LLC"))

	def test_a_doctype_the_user_may_not_read_yields_no_candidates_at_all(self):
		"""Not even the names. A candidate list is a list of records they cannot see."""
		doc, _ = self.extract(user=BLIND_DRAFTER, vendor=VENDOR_ACME)

		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertFalse(draft.vendor)

		candidate = self.candidates(doc)["vendor"]
		self.assertEqual(candidate["options"], [])
		self.assertIn("not allowed to read", candidate["note"])
		self.assertNotIn(VENDOR_ACME, frappe.as_json(candidate["options"]))

	def test_a_link_inside_a_child_row_resolves_by_its_own_key(self):
		"""The row index is part of the key, or two rows would answer for each other."""
		rows = [
			{"item": "FA Widget", "qty": 1, "row_vendor": VENDOR_ACME},
			{"item": "FA Widget", "qty": 2, "row_vendor": "FA Acme Trading H"},
		]
		doc, _ = self.extract(items=rows)

		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertEqual(draft.items[0].row_vendor, VENDOR_ACME)
		self.assertFalse(draft.items[1].row_vendor)
		self.assertIn("items[1].row_vendor", self.candidates(doc))
		self.assertNotIn("items[0].row_vendor", self.candidates(doc))

	def test_the_unresolved_text_stays_on_the_extraction_for_the_reviewer(self):
		"""Dropping the field is not the same as dropping the evidence."""
		doc, _ = self.extract(vendor="FA Nobody Ltd")

		self.assertEqual(self.candidates(doc)["vendor"]["text"], "FA Nobody Ltd")
		self.assertNotIn("vendor", extraction_json(doc, "extracted_json"))
