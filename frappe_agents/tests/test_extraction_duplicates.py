# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The two duplicate checks that need no knowledge of any particular doctype.

The same file extracted into the same doctype twice, and an extracted value that
collides with a `unique` field. Both are flags for a human: a supplier really can
send the same invoice twice, and the reviewer is the one who knows whether this
is that.

`content_hash` is the right key precisely because frappe dedupes blobs — two File
rows over the same bytes legitimately share one hash and one `file_url`, which is
what the second upload of the same invoice looks like.
"""

import frappe

from frappe_agents.api import discard_extraction
from frappe_agents.extraction.gate import find_duplicates
from frappe_agents.tests.fixtures import (
	DRAFT_USER,
	ORDER_DT,
	PROJECT_ALPHA,
	PROJECT_BETA,
	RESTRICTED_USER,
	VENDOR_ACME,
	VENDOR_DT,
	AgentTestCase,
	as_user,
	extract_as,
	extraction_json,
	extraction_reply,
	make_pdf,
	make_pdf_attachment,
)


class TestExtractionDuplicates(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.content = make_pdf("duplicate invoice")

	def extract(self, file, **extra):
		values = {
			"order_title": f"FA Extracted {frappe.generate_hash(length=8)}",
			"project": PROJECT_ALPHA,
			"vendor": VENDOR_ACME,
			"amount": 400,
		}
		values.update(extra)
		return extract_as(DRAFT_USER, file.name, extraction_reply(values))

	def duplicates(self, doc) -> dict:
		return extraction_json(doc, "duplicate_flags")

	def test_the_same_bytes_extracted_twice_are_flagged_the_second_time(self):
		first_file = make_pdf_attachment(content=self.content)
		first, _ = self.extract(first_file)

		second_file = make_pdf_attachment(content=self.content)
		self.assertEqual(second_file.content_hash, first_file.content_hash)

		second, _ = self.extract(second_file)

		same_file = self.duplicates(second)["same_file"]
		self.assertEqual([row["name"] for row in same_file], [first.name])
		self.assertEqual(same_file[0]["created_doc"], first.created_doc)

	def test_the_first_extraction_is_not_a_duplicate_of_itself(self):
		first, _ = self.extract(make_pdf_attachment(content=self.content))

		self.assertEqual(self.duplicates(first)["same_file"], [])

	def test_a_discarded_extraction_of_the_same_file_is_a_retry_not_a_duplicate(self):
		first_file = make_pdf_attachment(content=self.content)
		first, _ = self.extract(first_file)

		with as_user(DRAFT_USER):
			discard_extraction(first.name)

		second, _ = self.extract(make_pdf_attachment(content=self.content))

		self.assertEqual(self.duplicates(second)["same_file"], [])

	def test_the_same_file_into_a_different_doctype_is_not_a_duplicate(self):
		file = make_pdf_attachment(content=self.content)
		self.extract(file)

		second, _ = extract_as(
			DRAFT_USER,
			make_pdf_attachment(content=self.content).name,
			extraction_reply({"vendor_name": f"FA Vendor {frappe.generate_hash(length=6)}"}),
			target_doctype=VENDOR_DT,
		)

		self.assertEqual(self.duplicates(second)["same_file"], [])

	def test_a_value_that_collides_with_a_unique_field_is_flagged(self):
		"""The collision is recorded even though the database then refuses the draft.

		Which is the honest state of it: the flag is a banner for a reviewer, and the
		unique index is a wall. The extraction ends Failed with the collision on the
		row, rather than as a draft nobody can save.
		"""
		doc, _ = extract_as(
			DRAFT_USER,
			make_pdf_attachment(content=self.content).name,
			extraction_reply({"vendor_name": VENDOR_ACME}),
			target_doctype=VENDOR_DT,
		)

		collisions = self.duplicates(doc)["unique_collisions"]
		self.assertEqual(len(collisions), 1)
		self.assertEqual(collisions[0]["fieldname"], "vendor_name")
		self.assertEqual(collisions[0]["name"], VENDOR_ACME)

		self.assertEqual(doc.status, "Failed")
		self.assertFalse(doc.created_doc)

	def test_a_value_that_collides_with_nothing_is_not_flagged(self):
		doc, _ = extract_as(
			DRAFT_USER,
			make_pdf_attachment(content=self.content).name,
			extraction_reply({"vendor_name": f"FA Vendor {frappe.generate_hash(length=6)}"}),
			target_doctype=VENDOR_DT,
		)

		self.assertEqual(self.duplicates(doc)["unique_collisions"], [])
		self.assertEqual(doc.status, "Needs Review")

	def test_duplicate_flags_hide_unreadable_candidates(self):
		"""The check may look wider than the person it runs for. It may not tell them.

		One user extracts a private invoice into an order on a project the other
		cannot reach, and the vendor master is a doctype the other cannot read at
		all. Running both checks as that second user has to come back saying that
		something is already there and nothing whatsoever about what: no extraction
		name, no created document, no target name, no value.
		"""
		file = make_pdf_attachment(content=self.content)
		theirs, _ = self.extract(file, project=PROJECT_BETA)
		self.assertTrue(theirs.created_doc)

		with as_user(RESTRICTED_USER):
			same_file = find_duplicates(ORDER_DT, file.content_hash, {}, {})
			collision = find_duplicates(VENDOR_DT, None, {"vendor_name": VENDOR_ACME}, {})

		self.assertEqual(same_file["same_file"], [])
		self.assertEqual(same_file["same_file_hidden_count"], 1)
		self.assertEqual(collision["unique_collisions"], [])
		self.assertEqual(collision["unique_collisions_hidden_count"], 1)

		told = frappe.as_json([same_file, collision])
		for secret in (theirs.name, theirs.created_doc, VENDOR_ACME):
			self.assertNotIn(secret, told)

		# And the same two checks run by a user who may read those records name them.
		# What hides the rows above is the permission system, not a blanket refusal:
		# a duplicate check that told nobody anything would be no check at all.
		with as_user(DRAFT_USER):
			mine = find_duplicates(ORDER_DT, file.content_hash, {}, {})
			mine_collision = find_duplicates(VENDOR_DT, None, {"vendor_name": VENDOR_ACME}, {})

		self.assertEqual([row["name"] for row in mine["same_file"]], [theirs.name])
		self.assertEqual(mine["same_file"][0]["created_doc"], theirs.created_doc)
		self.assertEqual(mine["same_file_hidden_count"], 0)
		self.assertEqual(mine_collision["unique_collisions"][0]["name"], VENDOR_ACME)
		self.assertEqual(mine_collision["unique_collisions_hidden_count"], 0)

	def test_a_duplicate_is_a_flag_and_never_a_refusal(self):
		"""A supplier can send the same invoice twice. That is the reviewer's call."""
		self.extract(make_pdf_attachment(content=self.content))
		second, _ = self.extract(make_pdf_attachment(content=self.content))

		self.assertEqual(second.status, "Needs Review")
		self.assertTrue(second.created_doc)
		self.assertEqual(frappe.db.get_value(ORDER_DT, second.created_doc, "docstatus"), 0)
