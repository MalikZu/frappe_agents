# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Fill from file: the extraction pipeline, ending in a form instead of a draft.

What these tests hold the feature to:

* **Nothing is saved for the person.** The job stops at Ready with no insert, and
  the method that hands the values over writes nothing either.
* **Every extraction gate still refuses**, and refuses before the model is called:
  create or write permission, the file, the caps, the profile's file support.
* **The agent decides where the button works.** An Extract rule on the doctype,
  shown on forms, with a model — or a refusal.
* **Sensitive values never arrive as values.** They come back beside the form,
  flagged against the master and the link the comparison went through.
* **A reading is the owner's, and only while it is fresh.** Everything a stale row
  could be wrong about is asked again when the values are handed over.

The model call is mocked with canned JSON in the engine's own shape.
"""

from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, now_datetime

from frappe_agents.api import fill_from_file, get_fill_values, record_filled_document
from frappe_agents.boot import boot_form_agents
from frappe_agents.extraction.fill import discard_stale_fills
from frappe_agents.extraction.pipeline import EXTRACTION, queue_extraction
from frappe_agents.tests.fixtures import (
	ALTERED_IBAN,
	DRAFT_USER,
	EXTRACT_PROFILE,
	IBAN_FIELD,
	ORDER_DT,
	PROFILE,
	PROJECT_ALPHA,
	RESTRICTED_USER,
	SECOND_DRAFTER,
	TICKET_DT,
	VAULT_DT,
	VAULT_RECORD,
	VENDOR_ACME,
	VENDOR_DT,
	VENDOR_IBAN,
	AgentTestCase,
	as_user,
	extraction_reply,
	extraction_settings,
	make_legacy_agent,
	make_matrix_agent,
	make_order_draft,
	make_pdf,
	make_pdf_attachment,
	rule,
	run_extraction_with,
)

FILL_AGENT = "FA Fill Agent"
READY = "Ready"
ACCEPTED = "Accepted"
DISCARDED = "Discarded"
MODEL_CALL = "frappe_agents.extraction.pipeline.call_model_extract"


# The rule that turns the button on for orders.
EXTRACT_ORDERS = (rule(ORDER_DT, can_read=1, can_extract=1),)


def make_fill_agent(
	rules=EXTRACT_ORDERS,
	show_in_forms: int = 1,
	profile: str = EXTRACT_PROFILE,
	name: str = FILL_AGENT,
):
	"""A form agent whose Extract rule decides where Fill from file works."""
	agent = make_matrix_agent(rules=list(rules), name=name)
	agent.show_in_forms = show_in_forms
	agent.model_profile = profile
	agent.flags.ignore_permissions = True
	agent.save(ignore_permissions=True)
	frappe.clear_document_cache("Agent", agent.name)
	return agent


class TestFillFromFile(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.agent = make_fill_agent()
		self.file = make_pdf_attachment()
		self.orders_before = frappe.db.count(ORDER_DT)

	def fields(self, **extra) -> dict:
		values = {
			"order_title": f"FA Filled {frappe.generate_hash(length=8)}",
			"project": PROJECT_ALPHA,
			"vendor": VENDOR_ACME,
			"amount": 250,
			"items": [{"item": "FA Widget", "qty": 2}, {"item": "FA Gadget", "qty": 5}],
		}
		values.update(extra)
		return values

	def start(self, user: str = DRAFT_USER, agent: str = FILL_AGENT, file: str | None = None, **kwargs):
		with as_user(user), patch("frappe.enqueue"):
			return fill_from_file(agent, file or self.file.name, kwargs.pop("doctype", ORDER_DT), **kwargs)

	def read(self, reply: dict, user: str = DRAFT_USER, **kwargs) -> str:
		"""Start a fill and run its job with the model mocked. Returns the reading's name."""
		started = self.start(user=user, **kwargs)
		run_extraction_with(started["extraction"], reply)
		return started["extraction"]

	def values(self, extraction: str, user: str = DRAFT_USER) -> dict:
		with as_user(user):
			return get_fill_values(extraction)

	def refuse(self, user: str = DRAFT_USER, **kwargs) -> Exception:
		"""Ask for a fill and return the refusal, having proved nothing was sent or recorded."""
		rows_before = frappe.db.count(EXTRACTION)
		with patch(MODEL_CALL) as call, self.assertRaises(Exception) as caught:
			self.start(user=user, **kwargs)
		call.assert_not_called()
		self.assertEqual(frappe.db.count(EXTRACTION), rows_before)
		return caught.exception

	def assert_nothing_saved(self) -> None:
		self.assertEqual(frappe.db.count(ORDER_DT), self.orders_before)

	# --- the job stops before any insert ---------------------------------------

	def test_a_fill_stops_at_ready_and_inserts_nothing(self):
		extraction = self.read(extraction_reply(self.fields()))

		doc = frappe.get_doc(EXTRACTION, extraction)
		self.assertEqual(doc.status, READY)
		self.assertEqual(doc.purpose, "Fill Form")
		self.assertEqual(doc.agent, FILL_AGENT)
		self.assertEqual(doc.owner, DRAFT_USER)
		self.assertFalse(doc.created_doc)
		self.assert_nothing_saved()

	def test_the_values_come_back_without_saving_anything(self):
		fields = self.fields()
		extraction = self.read(
			extraction_reply(fields, [{"fieldname": "amount", "confidence": 0.4, "page": 1}])
		)

		answer = self.values(extraction)

		self.assertEqual(answer["status"], READY)
		self.assertEqual(answer["values"]["order_title"], fields["order_title"])
		self.assertEqual(answer["values"]["project"], PROJECT_ALPHA)
		self.assertEqual(answer["values"]["amount"], 250)
		self.assertEqual(answer["notes"]["amount"]["confidence"], 0.4)
		self.assertEqual(answer["source"]["file_name"], self.file.file_name)
		self.assert_nothing_saved()
		# Handing the values over is a read: the row is still waiting for its form.
		self.assertEqual(frappe.db.get_value(EXTRACTION, extraction, "status"), READY)

	def test_child_rows_are_mapped_for_the_form(self):
		extraction = self.read(extraction_reply(self.fields()))

		rows = self.values(extraction)["values"]["items"]

		self.assertEqual([row["item"] for row in rows], ["FA Widget", "FA Gadget"])
		self.assertEqual([row["qty"] for row in rows], [2, 5])

	def test_a_reading_still_running_says_so_and_hands_over_nothing(self):
		started = self.start()

		answer = self.values(started["extraction"])

		self.assertEqual(answer["status"], "Pending")
		self.assertNotIn("values", answer)

	def test_a_failed_reading_reports_its_reason(self):
		started = self.start()
		run_extraction_with(started["extraction"], {"data": None, "parse_error": "not JSON"})

		answer = self.values(started["extraction"])

		self.assertEqual(answer["status"], "Failed")
		self.assertTrue(answer["error"])

	# --- sensitive fields --------------------------------------------------------

	def test_a_sensitive_value_is_never_handed_over_as_a_value(self):
		extraction = self.read(extraction_reply(self.fields(**{IBAN_FIELD: ALTERED_IBAN})))

		answer = self.values(extraction)

		self.assertNotIn(IBAN_FIELD, answer["values"])
		self.assertNotIn(ALTERED_IBAN, frappe.as_json(answer["values"]))
		flag = answer["sensitive"][IBAN_FIELD]
		self.assertEqual(flag["value"], ALTERED_IBAN)
		self.assertEqual(flag["mismatch"], 1)
		self.assertEqual(flag["master_doctype"], VENDOR_DT)
		self.assertEqual(flag["master_name"], VENDOR_ACME)
		# The link the comparison went through, so the form can tell when it moves.
		self.assertEqual(flag["master_field"], "vendor")

	def test_the_iban_the_master_holds_is_not_a_mismatch(self):
		"""The control for the test above."""
		extraction = self.read(extraction_reply(self.fields(**{IBAN_FIELD: VENDOR_IBAN})))

		self.assertEqual(self.values(extraction)["sensitive"][IBAN_FIELD]["mismatch"], 0)

	def test_a_sensitive_value_inside_a_row_is_withheld_too(self):
		fields = self.fields(items=[{"item": "FA Widget", "qty": 1, IBAN_FIELD: ALTERED_IBAN}])
		extraction = self.read(extraction_reply(fields))

		answer = self.values(extraction)

		self.assertNotIn(IBAN_FIELD, answer["values"]["items"][0])
		self.assertEqual(answer["sensitive"][f"items[0].{IBAN_FIELD}"]["value"], ALTERED_IBAN)

	# --- refusals before the model is called -----------------------------------

	def test_without_create_permission_nothing_is_sent(self):
		error = self.refuse(user=RESTRICTED_USER)
		self.assertIsInstance(error, frappe.PermissionError)
		self.assertIn("create", str(error))

	def test_an_agent_without_an_extract_rule_is_refused(self):
		make_fill_agent(rules=[rule(ORDER_DT, can_read=1)])
		error = self.refuse()
		self.assertIsInstance(error, frappe.PermissionError)
		self.assertIn("extract into", str(error))

	def test_an_extract_rule_on_another_doctype_does_not_reach_this_one(self):
		make_fill_agent(rules=[rule(TICKET_DT, can_read=1, can_extract=1)])
		self.assertIsInstance(self.refuse(), frappe.PermissionError)

	def test_an_agent_not_offered_on_forms_is_refused(self):
		make_fill_agent(show_in_forms=0)
		self.assertIn("not offered on forms", str(self.refuse()))

	def test_a_legacy_agent_is_refused(self):
		legacy = make_legacy_agent()
		legacy.show_in_forms = 1
		legacy.model_profile = EXTRACT_PROFILE
		legacy.flags.ignore_permissions = True
		legacy.save(ignore_permissions=True)
		frappe.clear_document_cache("Agent", legacy.name)

		self.assertIsInstance(self.refuse(agent=legacy.name), frappe.PermissionError)

	def test_a_file_the_user_may_not_read_is_refused(self):
		file = make_pdf_attachment(VAULT_DT, VAULT_RECORD, content=make_pdf("vault"))
		self.assertIsInstance(self.refuse(file=file.name), frappe.PermissionError)

	def test_a_profile_that_cannot_read_pdfs_is_refused_up_front(self):
		"""The person at the form hears it now, not from a Failed row later."""
		make_fill_agent(profile=PROFILE)
		self.assertIn("Supports PDF", str(self.refuse()))

	def test_the_page_cap_refuses_before_anything_is_sent(self):
		file = make_pdf_attachment(content=make_pdf("long", pages=3))
		with extraction_settings(max_extraction_pages=2):
			self.assertIn("pages", str(self.refuse(file=file.name)))

	def test_the_daily_cap_counts_fills(self):
		with extraction_settings(extractions_per_user_per_day=1):
			self.start()
			self.assertIn("daily limit", str(self.refuse()))

	# --- a saved draft ------------------------------------------------------------

	def test_a_saved_draft_needs_write_permission_on_it(self):
		order = make_order_draft(user=DRAFT_USER)
		error = self.refuse(user=RESTRICTED_USER, docname=order.name)
		self.assertIsInstance(error, frappe.PermissionError)
		self.assertIn("not allowed to change", str(error))

	def test_a_submitted_document_cannot_be_filled(self):
		order = make_order_draft(user=DRAFT_USER)
		frappe.db.set_value(ORDER_DT, order.name, "docstatus", 1)
		self.assertIn("no longer a draft", str(self.refuse(docname=order.name)))

	def test_a_saved_draft_can_be_filled_by_the_person_who_may_change_it(self):
		order = make_order_draft(user=DRAFT_USER)
		started = self.start(docname=order.name)
		run_extraction_with(started["extraction"], extraction_reply(self.fields()))

		answer = self.values(started["extraction"])

		self.assertEqual(answer["target_name"], order.name)
		self.assertEqual(frappe.get_doc(ORDER_DT, order.name).amount, order.amount)

	# --- handing the values over --------------------------------------------------

	def test_another_user_cannot_fetch_the_values(self):
		extraction = self.read(extraction_reply(self.fields()))
		with self.assertRaises(frappe.PermissionError):
			self.values(extraction, user=SECOND_DRAFTER)

	def test_a_stale_reading_is_refused(self):
		extraction = self.read(extraction_reply(self.fields()))
		frappe.db.set_value(
			EXTRACTION, extraction, "creation", add_to_date(now_datetime(), hours=-25), update_modified=False
		)
		with self.assertRaises(frappe.ValidationError) as caught:
			self.values(extraction)
		self.assertIn("hours old", str(caught.exception))

	def test_a_rule_removed_after_the_reading_stops_the_handover(self):
		"""A reading done this morning is not a permission held now."""
		extraction = self.read(extraction_reply(self.fields()))
		make_fill_agent(rules=[rule(ORDER_DT, can_read=1)])

		with self.assertRaises(frappe.PermissionError):
			self.values(extraction)

	def test_a_draft_extraction_is_not_a_fill(self):
		with as_user(DRAFT_USER), patch("frappe.enqueue"):
			name = queue_extraction(self.file.name, ORDER_DT, model_profile=EXTRACT_PROFILE)
		with self.assertRaises(frappe.ValidationError):
			self.values(name)

	# --- after the save -----------------------------------------------------------

	def save_order(self, user: str = DRAFT_USER, **values):
		order = frappe.get_doc(
			{
				"doctype": ORDER_DT,
				"order_title": f"FA Saved {frappe.generate_hash(length=8)}",
				"project": PROJECT_ALPHA,
				"vendor": VENDOR_ACME,
				**values,
			}
		)
		with as_user(user):
			order.insert()
		return order

	def record(self, extraction: str, docname: str, user: str = DRAFT_USER) -> dict:
		with as_user(user):
			return record_filled_document(extraction, docname)

	def test_the_saved_document_is_recorded_and_the_source_attached(self):
		extraction = self.read(extraction_reply(self.fields(**{IBAN_FIELD: ALTERED_IBAN})))
		order = self.save_order(**{IBAN_FIELD: ALTERED_IBAN})

		done = self.record(extraction, order.name)

		self.assertEqual(done["status"], ACCEPTED)
		self.assertTrue(done["source_attached"])
		self.assertEqual(done["sensitive"][IBAN_FIELD], "printed")
		doc = frappe.get_doc(EXTRACTION, extraction)
		self.assertEqual(doc.created_doc, order.name)
		self.assertEqual(doc.reviewed_by, DRAFT_USER)
		flag = frappe.parse_json(doc.sensitive_flags)[IBAN_FIELD]
		self.assertEqual(flag["saved"], "printed")
		# Compared afresh against the vendor the saved order links to.
		self.assertEqual(flag["saved_mismatch"], 1)
		# The bank detail stays on the document; the flag records only how it compares.
		self.assertNotIn("applied_value", flag)
		self.assertTrue(
			frappe.db.exists(
				"File",
				{
					"file_url": self.file.file_url,
					"attached_to_doctype": ORDER_DT,
					"attached_to_name": order.name,
				},
			)
		)

	def test_a_sensitive_field_left_empty_is_recorded_as_empty(self):
		extraction = self.read(extraction_reply(self.fields(**{IBAN_FIELD: ALTERED_IBAN})))
		order = self.save_order()

		self.assertEqual(self.record(extraction, order.name)["sensitive"][IBAN_FIELD], "empty")

	def test_a_document_older_than_the_reading_cannot_claim_it(self):
		order = self.save_order()
		frappe.db.set_value(
			ORDER_DT, order.name, "creation", add_to_date(now_datetime(), hours=-1), update_modified=False
		)
		extraction = self.read(extraction_reply(self.fields()))

		with self.assertRaises(frappe.ValidationError):
			self.record(extraction, order.name)
		self.assertEqual(frappe.db.get_value(EXTRACTION, extraction, "status"), READY)

	def test_a_reading_for_one_draft_cannot_be_recorded_against_another(self):
		first = make_order_draft(user=DRAFT_USER)
		second = make_order_draft(user=DRAFT_USER)
		started = self.start(docname=first.name)
		run_extraction_with(started["extraction"], extraction_reply(self.fields()))

		with self.assertRaises(frappe.ValidationError):
			self.record(started["extraction"], second.name)

	def test_a_recorded_reading_cannot_be_recorded_again(self):
		extraction = self.read(extraction_reply(self.fields()))
		order = self.save_order()
		self.record(extraction, order.name)

		with self.assertRaises(frappe.ValidationError):
			self.record(extraction, order.name)

	# --- housekeeping -------------------------------------------------------------

	def test_readings_no_form_came_back_for_are_closed(self):
		extraction = self.read(extraction_reply(self.fields()))
		fresh = self.read(extraction_reply(self.fields()))
		frappe.db.set_value(
			EXTRACTION, extraction, "creation", add_to_date(now_datetime(), hours=-25), update_modified=False
		)

		discard_stale_fills()

		self.assertEqual(frappe.db.get_value(EXTRACTION, extraction, "status"), DISCARDED)
		self.assertEqual(frappe.db.get_value(EXTRACTION, fresh, "status"), READY)

	def test_the_boot_payload_names_the_doctypes_an_agent_may_fill(self):
		bootinfo = frappe._dict()
		with as_user(DRAFT_USER):
			boot_form_agents(bootinfo)

		agents = {agent["name"]: agent for agent in bootinfo["frappe_agents"]["form_agents"]}
		self.assertEqual(agents[FILL_AGENT]["extract_doctypes"], [ORDER_DT])
