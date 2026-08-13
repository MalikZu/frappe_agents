# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The sensitive-field gate: the one attack extraction exists to stop.

A genuine-looking invoice with the payment details changed. Everything else on a
document a reviewer catches by reading; a bank account can only be caught by
comparing, and only if the value never quietly reaches the draft in the meantime.

So these tests ask, in order: is the extracted IBAN kept off the draft, is it
flagged against what the master record says, and is the only door into the
document a person naming the field by hand.

No model is called: `call_model_extract` is patched with canned JSON in the shape
the engine actually reads — values under `fields`, confidence and page in a
`field_notes` array beside them.
"""

import frappe

from frappe_agents.api import apply_extraction
from frappe_agents.tests.fixtures import (
	ALTERED_IBAN,
	DRAFT_USER,
	IBAN_FIELD,
	ORDER_DT,
	PROJECT_ALPHA,
	VENDOR_ACME,
	VENDOR_DT,
	VENDOR_IBAN,
	AgentTestCase,
	as_user,
	extract_as,
	extraction_json,
	extraction_reply,
	make_pdf_attachment,
)

NEEDS_REVIEW = "Needs Review"
ACCEPTED = "Accepted"


class TestExtractionGate(AgentTestCase):
	def title(self) -> str:
		return f"FA Extracted {frappe.generate_hash(length=8)}"

	def fields(self, **extra) -> dict:
		"""What a canned model reply says it read off the invoice."""
		values = {
			"order_title": self.title(),
			"project": PROJECT_ALPHA,
			"vendor": VENDOR_ACME,
			"amount": 250,
			"notes": "Payment due in 30 days.",
			"items": [{"item": "FA Widget", "qty": 2}],
		}
		values.update(extra)
		return values

	def notes(self, *fieldnames: str) -> list[dict]:
		return [{"fieldname": name, "confidence": 0.91, "page": 1} for name in fieldnames]

	def extract(self, **extra) -> tuple:
		file = make_pdf_attachment()
		reply = extraction_reply(self.fields(**extra), self.notes("vendor", IBAN_FIELD))
		return extract_as(DRAFT_USER, file.name, reply)

	def flags(self, doc) -> dict:
		return extraction_json(doc, "sensitive_flags")

	# --- withholding ---------------------------------------------------------

	def test_a_sensitive_value_never_reaches_the_draft(self):
		doc, _ = self.extract(**{IBAN_FIELD: ALTERED_IBAN})

		self.assertEqual(doc.status, NEEDS_REVIEW)
		self.assertTrue(doc.created_doc)

		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertFalse(draft.get(IBAN_FIELD))
		self.assertNotIn(ALTERED_IBAN, frappe.as_json(draft.as_dict()))
		self.assertNotIn(IBAN_FIELD, extraction_json(doc, "extracted_json"))

	def test_the_withheld_value_is_kept_on_the_extraction_row_flagged(self):
		"""Withholding is not deleting: the reviewer has to see what the document said."""
		doc, _ = self.extract(**{IBAN_FIELD: ALTERED_IBAN})

		flag = self.flags(doc)[IBAN_FIELD]
		self.assertEqual(flag["value"], ALTERED_IBAN)
		self.assertEqual(flag["confirmed"], 0)

	def test_a_field_the_model_could_not_find_stays_absent_rather_than_written_empty(self):
		"""`""` means "not on the document", and writing it would blank a default."""
		doc, _ = self.extract(notes="", items=[])

		values = extraction_json(doc, "extracted_json")
		self.assertNotIn("notes", values)
		self.assertNotIn("items", values)

		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertFalse(draft.notes)
		self.assertEqual(len(draft.items), 0)

	def test_the_models_confidence_and_page_are_recorded_beside_the_values(self):
		doc, _ = self.extract(**{IBAN_FIELD: ALTERED_IBAN})

		notes = extraction_json(doc, "field_confidence")
		self.assertEqual(notes["vendor"]["confidence"], 0.91)
		self.assertEqual(notes["vendor"]["page"], 1)
		# Recorded for a withheld field too — it is a claim about the reading, not
		# about the writing.
		self.assertIn(IBAN_FIELD, notes)

	# --- the master comparison -----------------------------------------------

	def test_an_altered_iban_is_flagged_against_the_master(self):
		doc, _ = self.extract(**{IBAN_FIELD: ALTERED_IBAN})

		flag = self.flags(doc)[IBAN_FIELD]
		self.assertEqual(flag["mismatch"], 1)
		self.assertEqual(flag["master_doctype"], VENDOR_DT)
		self.assertEqual(flag["master_name"], VENDOR_ACME)

	def test_the_iban_the_master_carries_is_not_a_mismatch(self):
		"""The control. Without it, a gate that flags everything would pass the test above."""
		doc, _ = self.extract(**{IBAN_FIELD: VENDOR_IBAN})

		self.assertEqual(self.flags(doc)[IBAN_FIELD]["mismatch"], 0)

	def test_the_comparison_is_on_the_characters_not_the_typography(self):
		"""An IBAN is printed in groups and stored in one run. That is not an attack."""
		doc, _ = self.extract(**{IBAN_FIELD: VENDOR_IBAN.replace(" ", "").lower()})

		self.assertEqual(self.flags(doc)[IBAN_FIELD]["mismatch"], 0)

	def test_a_masked_master_value_is_compared_on_the_real_one(self):
		"""The v16 masking trap, which would make every extraction a mismatch forever.

		This user may read the vendor and may not see its IBAN unmasked. Read through
		the query layer it is "XXXXXXXX" — assert that first, or the rest of this test
		would keep passing after the mask was gone and prove nothing.
		"""
		with as_user(DRAFT_USER):
			through_query_layer = frappe.db.get_value(VENDOR_DT, VENDOR_ACME, IBAN_FIELD)
		self.assertEqual(through_query_layer, "XXXXXXXX")

		doc, _ = self.extract(**{IBAN_FIELD: VENDOR_IBAN})

		flag = self.flags(doc)[IBAN_FIELD]
		self.assertEqual(flag["mismatch"], 0)
		self.assertEqual(flag["master_name"], VENDOR_ACME)
		# The boolean is the safety signal; the value is a convenience this reviewer
		# has not earned, and `sensitive_flags` is a field they can read.
		self.assertEqual(flag["master_visible"], 0)
		self.assertIsNone(flag["master_value"])

	def test_a_masked_master_still_catches_the_altered_value(self):
		doc, _ = self.extract(**{IBAN_FIELD: ALTERED_IBAN})

		self.assertEqual(self.flags(doc)[IBAN_FIELD]["mismatch"], 1)

	def test_an_unresolved_vendor_leaves_the_value_flagged_with_nothing_to_compare(self):
		"""No master is "cannot tell", never "fine"."""
		doc, _ = self.extract(vendor="FA Nobody Ltd", **{IBAN_FIELD: ALTERED_IBAN})

		flag = self.flags(doc)[IBAN_FIELD]
		self.assertEqual(flag["value"], ALTERED_IBAN)
		self.assertIsNone(flag["master_name"])
		self.assertEqual(flag["mismatch"], 0)

		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertFalse(draft.get(IBAN_FIELD))

	# --- the same attack, one level down -------------------------------------

	def test_a_sensitive_value_in_a_child_row_is_gated_like_a_top_level_one(self):
		rows = [{"item": "FA Widget", "qty": 2, IBAN_FIELD: ALTERED_IBAN}]
		doc, _ = self.extract(items=rows)

		flag = self.flags(doc)[f"items[0].{IBAN_FIELD}"]
		self.assertEqual(flag["value"], ALTERED_IBAN)
		self.assertEqual(flag["confirmed"], 0)

		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertEqual(len(draft.items), 1)
		self.assertFalse(draft.items[0].get(IBAN_FIELD))
		self.assertNotIn(ALTERED_IBAN, frappe.as_json(draft.as_dict()))

	# --- the confirmation door -----------------------------------------------

	def test_applying_without_a_confirmation_leaves_the_field_empty(self):
		doc, _ = self.extract(**{IBAN_FIELD: ALTERED_IBAN})
		values = dict(extraction_json(doc, "extracted_json"))
		values[IBAN_FIELD] = ALTERED_IBAN

		with as_user(DRAFT_USER):
			result = apply_extraction(doc.name, values=values, confirmed=[])

		self.assertEqual(result["status"], ACCEPTED)
		self.assertEqual(result["withheld_fields"], [IBAN_FIELD])
		self.assertEqual(result["confirmed_fields"], [])

		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertFalse(draft.get(IBAN_FIELD))
		self.assertEqual(self.flags(frappe.get_doc(doc.doctype, doc.name))[IBAN_FIELD]["confirmed"], 0)

	def test_applying_with_a_confirmation_writes_the_value_and_records_who_confirmed_it(self):
		doc, _ = self.extract(**{IBAN_FIELD: ALTERED_IBAN})
		values = dict(extraction_json(doc, "extracted_json"))
		values[IBAN_FIELD] = ALTERED_IBAN

		with as_user(DRAFT_USER):
			result = apply_extraction(doc.name, values=values, confirmed=[IBAN_FIELD])

		self.assertEqual(result["confirmed_fields"], [IBAN_FIELD])
		self.assertEqual(result["withheld_fields"], [])

		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertEqual(draft.get(IBAN_FIELD), ALTERED_IBAN)

		flag = self.flags(frappe.get_doc(doc.doctype, doc.name))[IBAN_FIELD]
		self.assertEqual(flag["confirmed"], 1)
		self.assertEqual(flag["confirmed_by"], DRAFT_USER)
		self.assertEqual(flag["applied_value"], ALTERED_IBAN)
		self.assertTrue(flag["confirmed_at"])

	def test_a_confirmation_applies_the_value_the_reviewer_had_in_front_of_them(self):
		"""Confirming means "I checked this value", so their edit is what gets written."""
		doc, _ = self.extract(**{IBAN_FIELD: ALTERED_IBAN})
		values = dict(extraction_json(doc, "extracted_json"))
		values[IBAN_FIELD] = VENDOR_IBAN

		with as_user(DRAFT_USER):
			apply_extraction(doc.name, values=values, confirmed=[IBAN_FIELD])

		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertEqual(draft.get(IBAN_FIELD), VENDOR_IBAN)

	def test_confirming_a_child_row_value_writes_it_into_that_row(self):
		rows = [{"item": "FA Widget", "qty": 2, IBAN_FIELD: ALTERED_IBAN}]
		doc, _ = self.extract(items=rows)
		key = f"items[0].{IBAN_FIELD}"
		values = dict(extraction_json(doc, "extracted_json"))
		values[key] = ALTERED_IBAN

		with as_user(DRAFT_USER):
			result = apply_extraction(doc.name, values=values, confirmed=[key])

		self.assertEqual(result["confirmed_fields"], [key])
		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertEqual(draft.items[0].get(IBAN_FIELD), ALTERED_IBAN)

	def test_a_child_row_value_left_unconfirmed_stays_off_the_row(self):
		rows = [{"item": "FA Widget", "qty": 2, IBAN_FIELD: ALTERED_IBAN}]
		doc, _ = self.extract(items=rows)
		values = dict(extraction_json(doc, "extracted_json"))
		values[f"items[0].{IBAN_FIELD}"] = ALTERED_IBAN

		with as_user(DRAFT_USER):
			result = apply_extraction(doc.name, values=values, confirmed=[])

		self.assertEqual(result["withheld_fields"], [f"items[0].{IBAN_FIELD}"])
		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertFalse(draft.items[0].get(IBAN_FIELD))

	def test_a_confirmation_for_a_field_that_is_not_sensitive_is_refused(self):
		"""The confirmed list is not a second way to write ordinary fields."""
		doc, _ = self.extract(**{IBAN_FIELD: ALTERED_IBAN})

		with as_user(DRAFT_USER), self.assertRaises(frappe.ValidationError):
			apply_extraction(doc.name, values={"amount": 999}, confirmed=["amount"])

	def test_the_gate_end_to_end_on_an_altered_payment_detail(self):
		"""The whole gate in one story, because the parts are only worth what the sum is.

		A document arrives claiming a different IBAN than the vendor's own record. It
		is flagged, it is absent from the draft, an accept that does not name it does
		not write it, and only a named confirmation does — recorded.
		"""
		doc, _ = self.extract(**{IBAN_FIELD: ALTERED_IBAN})

		flag = self.flags(doc)[IBAN_FIELD]
		self.assertEqual(flag["mismatch"], 1)
		self.assertEqual(flag["value"], ALTERED_IBAN)

		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertFalse(draft.get(IBAN_FIELD))

		values = dict(extraction_json(doc, "extracted_json"), **{IBAN_FIELD: ALTERED_IBAN})
		with as_user(DRAFT_USER):
			apply_extraction(doc.name, values=values, confirmed=[])
		self.assertFalse(frappe.db.get_value(ORDER_DT, doc.created_doc, IBAN_FIELD))

		# The same document again, this time with the reviewer's hand on it.
		second, _ = self.extract(**{IBAN_FIELD: ALTERED_IBAN})
		values = dict(extraction_json(second, "extracted_json"), **{IBAN_FIELD: ALTERED_IBAN})
		with as_user(DRAFT_USER):
			apply_extraction(second.name, values=values, confirmed=[IBAN_FIELD])

		self.assertEqual(frappe.db.get_value(ORDER_DT, second.created_doc, IBAN_FIELD), ALTERED_IBAN)
		reloaded = frappe.get_doc(second.doctype, second.name)
		self.assertEqual(reloaded.status, ACCEPTED)
		self.assertEqual(reloaded.reviewed_by, DRAFT_USER)
		self.assertEqual(self.flags(reloaded)[IBAN_FIELD]["confirmed"], 1)
