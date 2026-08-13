# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Everything that refuses an extraction before a model is ever called.

Permission on the file, permission to create the target, the size cap, the page
cap, the daily cap. The order is the design: a refusal costs nothing, arrives as
a sentence to the person who asked rather than a status field they find later,
and a hostile file never reaches a provider on someone else's authority.

Every test here asserts the mock was **not** called. That assertion is the test —
without it, "it threw" says nothing about whether the document was sent first.
"""

from unittest.mock import patch

import frappe

from frappe_agents.api import start_extraction
from frappe_agents.extraction.pipeline import EXTRACTION, queue_extraction
from frappe_agents.tests.fixtures import (
	DRAFT_USER,
	EXTRACT_PROFILE,
	ORDER_DT,
	ORDER_LIVE,
	PROFILE,
	RESTRICTED_USER,
	VAULT_DT,
	VAULT_RECORD,
	AgentTestCase,
	as_user,
	extraction_settings,
	make_pdf,
	make_pdf_attachment,
	set_kill_switch,
)


class TestExtractionLimits(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.file = make_pdf_attachment()

	def refuse(self, user: str = DRAFT_USER, file_name: str | None = None, target: str = ORDER_DT):
		"""Ask for an extraction and return the refusal, having proved no model ran."""
		with (
			patch("frappe_agents.extraction.pipeline.call_model_extract") as call,
			patch("frappe.enqueue") as enqueue,
			as_user(user),
			self.assertRaises(Exception) as caught,
		):
			queue_extraction(file_name or self.file.name, target, model_profile=EXTRACT_PROFILE)

		call.assert_not_called()
		enqueue.assert_not_called()
		return caught.exception

	# --- permission ----------------------------------------------------------

	def test_without_create_permission_on_the_target_nothing_is_sent(self):
		"""The restricted user may read orders. Reading is not a reason to extract into one."""
		error = self.refuse(user=RESTRICTED_USER)

		self.assertIsInstance(error, frappe.PermissionError)
		self.assertIn(ORDER_DT, str(error))
		self.assertFalse(frappe.db.exists(EXTRACTION, {"owner": RESTRICTED_USER}))

	def test_a_file_the_user_may_not_read_is_refused(self):
		"""A private file hanging off a document nobody in the cast may read.

		Roles alone grant nothing on a File: the reviewer's right to it comes from the
		record it is attached to, and this one is attached to the vault.
		"""
		file = make_pdf_attachment(VAULT_DT, VAULT_RECORD, content=make_pdf("unreadable"))

		error = self.refuse(file_name=file.name)

		self.assertIsInstance(error, frappe.PermissionError)

	def test_a_missing_file_is_a_sentence_not_a_traceback(self):
		error = self.refuse(file_name="no-such-file")

		self.assertIn("No such file", str(error))

	def test_a_target_that_is_not_a_doctype_is_refused(self):
		error = self.refuse(target="FA Not A DocType")

		self.assertIn("No such DocType", str(error))

	def test_the_kill_switch_stops_an_extraction_before_the_file_is_read(self):
		set_kill_switch(0)
		self.addCleanup(set_kill_switch, 1)

		error = self.refuse()

		self.assertIn("switched off", str(error))

	def test_a_model_profile_the_user_may_not_use_is_refused(self):
		profile = frappe.get_doc("LLM Model Profile", EXTRACT_PROFILE)
		profile.append("allowed_roles", {"role": "Agent Manager"})
		profile.flags.ignore_permissions = True
		profile.save(ignore_permissions=True)
		frappe.clear_document_cache("LLM Model Profile", EXTRACT_PROFILE)

		error = self.refuse()

		self.assertIsInstance(error, frappe.PermissionError)
		self.assertIn(EXTRACT_PROFILE, str(error))

	def test_an_ambiguous_profile_is_a_question_rather_than_a_guess(self):
		"""Two profiles this user may use, and no way to know which one they meant."""
		with (
			patch("frappe_agents.extraction.pipeline.call_model_extract") as call,
			patch("frappe.enqueue"),
			as_user(DRAFT_USER),
			self.assertRaises(frappe.ValidationError) as caught,
		):
			queue_extraction(self.file.name, ORDER_DT, model_profile=None)

		call.assert_not_called()
		self.assertIn(EXTRACT_PROFILE, str(caught.exception))
		self.assertIn(PROFILE, str(caught.exception))

	# --- caps ----------------------------------------------------------------

	def test_a_pdf_over_the_page_cap_is_refused_in_words(self):
		file = make_pdf_attachment(content=make_pdf("long", pages=6))

		with extraction_settings(max_extraction_pages=5):
			error = self.refuse(file_name=file.name)

		self.assertIn("6 pages", str(error))
		self.assertIn("limited to 5", str(error))

	def test_a_file_over_the_size_cap_is_refused_in_words(self):
		file = make_pdf_attachment(content=make_pdf("fat", pad=1_500_000))

		with extraction_settings(max_extraction_file_mb=1):
			error = self.refuse(file_name=file.name)

		self.assertIn("limited to 1 MB", str(error))

	def test_the_size_cap_is_measured_on_the_bytes_not_on_the_file_size_field(self):
		"""`File.file_size` is written from the request's own form data. It is not evidence."""
		file = make_pdf_attachment(content=make_pdf("fat", pad=1_500_000))
		frappe.db.set_value("File", file.name, "file_size", 10, update_modified=False)

		with extraction_settings(max_extraction_file_mb=1):
			error = self.refuse(file_name=file.name)

		self.assertIn("MB", str(error))

	def test_a_file_that_is_not_a_document_is_refused(self):
		"""The type is sniffed from the bytes, so an unreadable one is named as such.

		Not with a `.pdf` extension over text: frappe's own File validation opens a
		file called PDF with pypdf and rejects it before extraction ever sees it.
		"""
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "fa-not-a-document.txt",
				"attached_to_doctype": ORDER_DT,
				"attached_to_name": ORDER_LIVE,
				"is_private": 1,
				"content": b"this is a text file wearing a pdf name",
			}
		)
		file.flags.ignore_permissions = True
		file.insert(ignore_permissions=True)

		error = self.refuse(file_name=file.name)

		self.assertIn("PDFs and images", str(error))

	def test_the_daily_cap_counts_this_users_extractions_and_fails_closed(self):
		with extraction_settings(extractions_per_user_per_day=1):
			with patch("frappe.enqueue"), as_user(DRAFT_USER):
				first = queue_extraction(self.file.name, ORDER_DT, model_profile=EXTRACT_PROFILE)
			self.assertTrue(first)

			error = self.refuse()

		self.assertIn("daily limit", str(error))

	def test_the_daily_cap_is_per_user(self):
		"""One person's busy day is not another person's refusal."""
		with extraction_settings(extractions_per_user_per_day=1):
			with patch("frappe.enqueue"), as_user(DRAFT_USER):
				queue_extraction(self.file.name, ORDER_DT, model_profile=EXTRACT_PROFILE)

			with patch("frappe.enqueue"), as_user("Administrator"):
				second = queue_extraction(self.file.name, ORDER_DT, model_profile=EXTRACT_PROFILE)

		self.assertTrue(second)

	# --- the same refusals through the whitelisted endpoint -------------------

	def test_the_endpoint_refuses_exactly_as_the_queue_does(self):
		with (
			patch("frappe_agents.extraction.pipeline.call_model_extract") as call,
			patch("frappe.enqueue"),
			as_user(RESTRICTED_USER),
			self.assertRaises(frappe.PermissionError),
		):
			start_extraction(self.file.name, ORDER_DT, model_profile=EXTRACT_PROFILE)

		call.assert_not_called()

	def test_the_endpoint_returns_a_pending_extraction(self):
		with patch("frappe.enqueue") as enqueue, as_user(DRAFT_USER):
			result = start_extraction(self.file.name, ORDER_DT, model_profile=EXTRACT_PROFILE)

		self.assertEqual(result["status"], "Pending")
		self.assertEqual(frappe.db.get_value(EXTRACTION, result["extraction"], "owner"), DRAFT_USER)
		self.assertEqual(enqueue.call_count, 1)
