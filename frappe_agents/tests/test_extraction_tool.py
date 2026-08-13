# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The one tool that starts an extraction, and everything it is not allowed to do.

An agent may ask for a document to be read. It does not see the result and it
does not act on it: the tool returns a name, the values land on a record a person
reviews, and the sensitive ones do not land anywhere until that person says so.

Two narrowings beyond the File permission model are asserted here. The file must
be attached to a record the user can read — a loose private file has no
provenance a reviewer could check. And the payload is a file and a doctype and
nothing else, so no text a model writes can reach the extraction call.
"""

from unittest.mock import patch

import frappe

from frappe_agents.tests.fixtures import (
	AGENT,
	ALTERED_IBAN,
	DRAFT_AGENT,
	DRAFT_USER,
	IBAN_FIELD,
	ORDER_DT,
	ORDER_LIVE,
	RESTRICTED_USER,
	VAULT_DT,
	VAULT_RECORD,
	AgentTestCase,
	as_user,
	call_tool,
	extraction_reply,
	make_pdf,
	make_pdf_attachment,
	make_run,
	run_extraction_with,
	tool_calls_for,
)


class TestExtractionTool(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.file = make_pdf_attachment()

	def extract(self, user: str = DRAFT_USER, agent: str = DRAFT_AGENT, **args):
		payload = {"file": self.file.name, "target_doctype": ORDER_DT}
		payload.update(args)
		with patch("frappe.enqueue"):
			return call_tool(user, "extract_document", payload, agent=agent)

	def test_the_tool_queues_an_extraction_and_returns_only_its_name(self):
		payload, run = self.extract()

		self.assertTrue(payload["ok"], payload["error"])
		result = payload["result"]
		self.assertTrue(result["extraction"])
		self.assertEqual(result["status"], "Pending")
		self.assertEqual(
			frappe.db.get_value("Document Extraction", result["extraction"], "owner"), DRAFT_USER
		)

		calls = tool_calls_for(run.name)
		self.assertEqual(calls[0].outcome, "Success")
		self.assertIn(result["extraction"], calls[0].docs_touched)

	def test_the_agent_never_sees_an_extracted_value(self):
		"""The result of a reading the agent asked for is not the agent's to read."""
		payload, _ = self.extract()
		extraction = payload["result"]["extraction"]

		doc, _ = run_extraction_with(
			extraction,
			extraction_reply({"order_title": "FA Extracted by tool", **{IBAN_FIELD: ALTERED_IBAN}}),
		)
		self.assertEqual(doc.status, "Needs Review")

		# What the agent was told, before and after the job ran.
		self.assertNotIn(ALTERED_IBAN, frappe.as_json(payload["result"]))
		self.assertNotIn("values", payload["result"])

	def test_a_file_attached_to_nothing_is_refused_for_want_of_provenance(self):
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "fa-loose-invoice.pdf",
				"is_private": 1,
				"content": make_pdf("loose"),
			}
		)
		file.flags.ignore_permissions = True
		file.insert(ignore_permissions=True)
		# Readable — they uploaded it. The tool still refuses it: readable is not the
		# same as accountable, and a loose private file has no provenance to check.
		frappe.db.set_value("File", file.name, "owner", DRAFT_USER, update_modified=False)
		frappe.clear_document_cache("File", file.name)

		payload, _ = self.extract(file=file.name)

		self.assertFalse(payload["ok"])
		self.assertIn("not attached to any record", payload["error"])

	def test_a_file_on_a_record_the_user_may_not_read_is_refused(self):
		file = make_pdf_attachment(VAULT_DT, VAULT_RECORD, content=make_pdf("vaulted"))

		payload, _ = self.extract(file=file.name)

		self.assertFalse(payload["ok"])
		self.assertFalse(frappe.db.exists("Document Extraction", {"source_file": file.name}))

	def test_a_user_without_create_permission_is_denied_and_logged(self):
		payload, run = self.extract(user=RESTRICTED_USER)

		self.assertFalse(payload["ok"])
		self.assertIn(ORDER_DT, payload["error"])
		self.assertEqual(tool_calls_for(run.name)[0].outcome, "Denied")

	def test_a_suggest_agent_cannot_start_an_extraction(self):
		"""Extraction writes a draft, so it is a Draft capability like any other."""
		run = make_run(effective_user=DRAFT_USER, agent=AGENT)
		with patch("frappe.enqueue"):
			payload, _ = call_tool(
				DRAFT_USER,
				"extract_document",
				{"file": self.file.name, "target_doctype": ORDER_DT},
				run=run,
			)

		self.assertFalse(payload["ok"])
		self.assertIn("Suggest", payload["error"])
		self.assertFalse(frappe.db.exists("Document Extraction", {"source_file": self.file.name}))

	def test_the_payload_has_no_room_for_instructions(self):
		"""A prompt smuggled through the tool call is an argument the schema refuses."""
		tool = frappe.get_doc("Agent Tool", "extract_document")
		schema = frappe.parse_json(tool.args_schema)

		self.assertEqual(sorted(schema["properties"]), ["file", "target_doctype"])
		self.assertEqual(sorted(schema["required"]), ["file", "target_doctype"])

	def test_the_source_file_is_attached_to_the_extraction_for_the_reviewer(self):
		"""A reviewer who did not upload the PDF would otherwise get a blank pane."""
		payload, _ = self.extract()
		extraction = payload["result"]["extraction"]

		copies = frappe.get_all(
			"File",
			filters={"attached_to_doctype": "Document Extraction", "attached_to_name": extraction},
			fields=["name", "file_url"],
		)
		self.assertEqual(len(copies), 1)
		self.assertEqual(copies[0].file_url, self.file.file_url)

	def test_the_tool_refuses_a_payload_that_is_not_a_file_and_a_doctype(self):
		with as_user(DRAFT_USER):
			payload, _ = self.extract(file="")

		self.assertFalse(payload["ok"])
		self.assertIn("file", payload["error"])
		self.assertTrue(frappe.db.exists(ORDER_DT, ORDER_LIVE))
