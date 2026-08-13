# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The extraction call itself: what goes on the wire, and what happens when the
answer is not usable.

The invariant these tests exist for is the structural one from the RFC: **the
extraction call binds no tools.** The document is hostile input. With no tool
bound, the worst a document that tries to give orders can do is put wrong text in
a field a human is about to read.

So the no-tools tests do not mock `call_model_extract` — they mock the HTTP post
underneath it and read the payload that would have been sent, queued by an agent
that holds every tool in the fixture set. A mock of the function whose payload we
are asserting about would prove nothing.
"""

from unittest.mock import patch

import frappe

from frappe_agents.extraction.pipeline import EXTRACTION, run_extraction
from frappe_agents.runner.providers import EXTRACT_MAX_TOKENS, ExtractionRefused, ProviderError
from frappe_agents.tests.fixtures import (
	DRAFT_AGENT,
	DRAFT_USER,
	EXTRACT_PROFILE,
	ORDER_DT,
	PROFILE,
	PROJECT_ALPHA,
	VENDOR_ACME,
	AgentTestCase,
	as_user,
	call_tool,
	extract_as,
	extraction_reply,
	make_pdf_attachment,
	queue_extraction_as,
	run_extraction_with,
	unusable_reply,
)

TOOL_KEYS = ("tools", "tool_choice", "functions", "function_call")


def canned_completion(fields: dict) -> dict:
	"""An OpenAI-compatible response body carrying schema-conforming JSON."""
	content = frappe.as_json({"fields": fields, "field_notes": []})
	return {
		"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
		"usage": {"prompt_tokens": 4321, "completion_tokens": 210},
	}


class TestExtractionCalls(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.file = make_pdf_attachment()

	def use_extract_profile(self, agent: str) -> None:
		"""Point an agent at the profile that is declared able to read PDFs."""
		frappe.db.set_value("Agent", agent, "model_profile", EXTRACT_PROFILE, update_modified=False)
		frappe.clear_document_cache("Agent", agent)
		self.addCleanup(frappe.clear_document_cache, "Agent", agent)

	def fields(self) -> dict:
		return {
			"order_title": f"FA Extracted {frappe.generate_hash(length=8)}",
			"project": PROJECT_ALPHA,
			"vendor": VENDOR_ACME,
			"amount": 120,
		}

	# --- no tools, ever ------------------------------------------------------

	def test_the_extraction_request_binds_no_tools(self):
		"""Queued by an agent holding every tool there is. The request carries none."""
		agent = frappe.get_doc("Agent", DRAFT_AGENT)
		self.assertTrue(agent.get("tools"))
		self.use_extract_profile(DRAFT_AGENT)

		payload, run = call_tool(
			DRAFT_USER,
			"extract_document",
			{"file": self.file.name, "target_doctype": ORDER_DT},
			agent=DRAFT_AGENT,
		)
		self.assertTrue(payload["ok"], payload["error"])
		extraction = payload["result"]["extraction"]

		with patch("frappe_agents.runner.providers._post") as post:
			post.return_value = canned_completion(self.fields())
			run_extraction(extraction)

		sent = post.call_args.args[2]
		for key in TOOL_KEYS:
			self.assertNotIn(key, sent)
		self.assertNotIn('"tools"', frappe.as_json(sent))
		self.assertEqual(sent["response_format"]["type"], "json_schema")
		self.assertEqual(sent["max_tokens"], EXTRACT_MAX_TOKENS)

		doc = frappe.get_doc(EXTRACTION, extraction)
		self.assertEqual(doc.status, "Needs Review")
		self.assertEqual(doc.tokens_in, 4321)
		self.assertTrue(run.name)

	def test_the_document_travels_in_the_request_and_nowhere_else(self):
		"""The bytes go into the body. Not into a field, not into a log line."""
		extraction = queue_extraction_as(DRAFT_USER, self.file.name)

		with patch("frappe_agents.runner.providers._post") as post:
			post.return_value = canned_completion(self.fields())
			run_extraction(extraction)

		part = post.call_args.args[2]["messages"][-1]["content"][-1]
		self.assertEqual(part["type"], "file")
		self.assertTrue(part["file"]["file_data"].startswith("data:application/pdf;base64,"))

		doc = frappe.get_doc(EXTRACTION, extraction)
		self.assertNotIn("base64", frappe.as_json(doc.as_dict()))

	def test_the_instructions_come_from_the_engine_not_from_a_caller(self):
		"""There is no free-text channel from a model into the extraction call."""
		_, call = extract_as(DRAFT_USER, self.file.name, extraction_reply(self.fields()))

		instructions = call.call_args.args[5]
		self.assertIn(ORDER_DT, instructions)
		self.assertIn("Leave anything you cannot find empty", instructions)

	def test_the_parser_engine_that_ran_is_recorded_on_the_row(self):
		"""Which engine read the PDF is a security fact, so it is never left implied."""
		doc, _ = extract_as(DRAFT_USER, self.file.name, extraction_reply(self.fields()))

		self.assertEqual(doc.parser_engine, "native")

	# --- when the answer is not usable ---------------------------------------

	def test_an_unusable_reply_is_asked_once_more_and_then_recorded_as_failed(self):
		doc, call = extract_as(
			DRAFT_USER,
			self.file.name,
			side_effect=[unusable_reply(), unusable_reply()],
		)

		self.assertEqual(call.call_count, 2)
		self.assertEqual(doc.status, "Failed")
		self.assertIn("not valid JSON", doc.error)
		self.assertFalse(doc.created_doc)

	def test_the_tokens_a_failed_extraction_cost_are_still_recorded(self):
		"""A failed extraction still cost money, and a cost nobody can see is a cost nobody manages."""
		doc, _ = extract_as(
			DRAFT_USER,
			self.file.name,
			side_effect=[unusable_reply(), unusable_reply()],
		)

		self.assertEqual(doc.tokens_in, 1800)
		self.assertEqual(doc.tokens_out, 240)

	def test_the_second_ask_quotes_the_error_and_can_succeed(self):
		doc, call = extract_as(
			DRAFT_USER,
			self.file.name,
			side_effect=[unusable_reply(), extraction_reply(self.fields())],
		)

		self.assertEqual(call.call_count, 2)
		self.assertIn("could not be used", call.call_args.args[5])
		self.assertEqual(doc.status, "Needs Review")
		self.assertTrue(doc.created_doc)
		# Both calls counted, not just the one that worked.
		self.assertEqual(doc.tokens_in, 2100)

	def test_a_refusal_is_an_answer_and_is_never_re_asked(self):
		refusal = ExtractionRefused("The model declined to extract this document.", 700, 30)
		doc, call = extract_as(DRAFT_USER, self.file.name, side_effect=refusal)

		self.assertEqual(call.call_count, 1)
		self.assertEqual(doc.status, "Failed")
		self.assertIn("declined", doc.error)
		self.assertEqual(doc.tokens_in, 700)
		self.assertEqual(doc.tokens_out, 30)

	def test_a_provider_error_fails_the_row_instead_of_raising_into_the_worker(self):
		doc, _ = extract_as(
			DRAFT_USER,
			self.file.name,
			side_effect=ProviderError("Model request to http://localhost:1/v1 failed."),
		)

		self.assertEqual(doc.status, "Failed")
		self.assertIn("Model request", doc.error)

	def test_a_profile_that_was_never_declared_able_to_read_pdfs_is_refused(self):
		"""A generic endpoint cannot be probed, so extraction fails closed on the declaration."""
		extraction = queue_extraction_as(DRAFT_USER, self.file.name, profile=PROFILE)

		with patch("frappe_agents.runner.providers._post") as post:
			run_extraction(extraction)

		post.assert_not_called()
		doc = frappe.get_doc(EXTRACTION, extraction)
		self.assertEqual(doc.status, "Failed")
		self.assertIn("Supports PDF", doc.error)

	# --- a job that should not run -------------------------------------------

	def test_a_requeued_job_never_re_extracts_a_reviewed_document(self):
		extraction = queue_extraction_as(DRAFT_USER, self.file.name)
		doc, first = run_extraction_with(extraction, extraction_reply(self.fields()))
		self.assertEqual(doc.status, "Needs Review")
		created = doc.created_doc

		doc, second = run_extraction_with(extraction, extraction_reply(self.fields()))

		second.assert_not_called()
		self.assertEqual(doc.status, "Needs Review")
		self.assertEqual(doc.created_doc, created)
		self.assertEqual(first.call_count, 1)

	def test_the_job_refuses_to_run_as_a_disabled_user(self):
		extraction = queue_extraction_as(DRAFT_USER, self.file.name)
		frappe.db.set_value("User", DRAFT_USER, "enabled", 0, update_modified=False)
		frappe.clear_cache(user=DRAFT_USER)
		self.addCleanup(frappe.clear_cache, user=DRAFT_USER)

		doc, call = run_extraction_with(extraction, extraction_reply(self.fields()))

		call.assert_not_called()
		self.assertEqual(doc.status, "Failed")
		self.assertIn("disabled", doc.error)

	def test_the_job_runs_as_the_person_who_asked(self):
		"""Not as the worker. Every permission check in the pipeline depends on it."""
		extraction = queue_extraction_as(DRAFT_USER, self.file.name)
		seen = []

		def remember(*args, **kwargs):
			seen.append(frappe.session.user)
			return extraction_reply(self.fields())

		with as_user("Administrator"):
			run_extraction_with(extraction, side_effect=remember)

		self.assertEqual(seen, [DRAFT_USER])
		self.assertEqual(frappe.session.user, "Administrator")
