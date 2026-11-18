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
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.utils import add_to_date, now_datetime

from frappe_agents.api import discard_extraction, fill_from_file, get_fill_values, record_filled_document
from frappe_agents.boot import boot_form_agents
from frappe_agents.extraction.fill import TOKEN_KEY, discard_stale_fills
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
	VENDOR_ACME_HOLDINGS,
	VENDOR_DT,
	VENDOR_IBAN,
	AgentTestCase,
	as_user,
	extract_as,
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
from frappe_agents.tools.base import masking_supported

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


class FillTestCase(AgentTestCase):
	"""A form agent with an Extract rule on orders, a PDF on a live order, and helpers."""

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


class TestFillFromFile(FillTestCase):
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
		"""The control for the test above, and the one case offered as a single click."""
		extraction = self.read(extraction_reply(self.fields(**{IBAN_FIELD: VENDOR_IBAN})))

		flag = self.values(extraction)["sensitive"][IBAN_FIELD]
		self.assertEqual(flag["mismatch"], 0)
		self.assertEqual(flag["compared"], 1)
		self.assertEqual(flag["blocked"], 0)

	def test_an_altered_iban_cannot_be_used_with_one_click(self):
		extraction = self.read(extraction_reply(self.fields(**{IBAN_FIELD: ALTERED_IBAN})))

		self.assertEqual(self.values(extraction)["sensitive"][IBAN_FIELD]["blocked"], 1)

	def test_an_iban_compared_with_nothing_is_not_agreement(self):
		"""No linked record to compare with is not a match, and gets no shortcut."""
		fields = self.fields(**{IBAN_FIELD: ALTERED_IBAN})
		fields.pop("vendor")
		extraction = self.read(extraction_reply(fields))

		flag = self.values(extraction)["sensitive"][IBAN_FIELD]
		self.assertEqual(flag["compared"], 0)
		self.assertEqual(flag["mismatch"], 0)
		self.assertEqual(flag["blocked"], 1)

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

	def test_the_saved_document_is_recorded_and_the_source_attached(self):
		extraction = self.read(extraction_reply(self.fields(**{IBAN_FIELD: ALTERED_IBAN})))
		order = self.save_order(**{IBAN_FIELD: ALTERED_IBAN})
		before = frappe.db.count(ORDER_DT)

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
		self.assertEqual(frappe.db.count(ORDER_DT), before)
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

	def test_a_typed_value_is_recorded_as_typed_and_not_copied(self):
		"""The bank detail stays on the document; the row records only how it compares."""
		typed = "AE07 0331 1111 2222 3333 444"
		extraction = self.read(extraction_reply(self.fields(**{IBAN_FIELD: ALTERED_IBAN})))
		order = self.save_order(**{IBAN_FIELD: typed})

		self.assertEqual(self.record(extraction, order.name)["sensitive"][IBAN_FIELD], "typed")
		stored = frappe.db.get_value(EXTRACTION, extraction, "sensitive_flags")
		self.assertNotIn(typed, stored)
		self.assertNotIn(typed.replace(" ", ""), stored)

	def test_another_user_cannot_record_a_reading(self):
		extraction = self.read(extraction_reply(self.fields()))
		order = self.save_order(user=SECOND_DRAFTER)

		with self.assertRaises(frappe.PermissionError):
			self.record(extraction, order.name, user=SECOND_DRAFTER)
		self.assertEqual(frappe.db.get_value(EXTRACTION, extraction, "status"), READY)

	def test_another_users_document_cannot_claim_a_reading(self):
		extraction = self.read(extraction_reply(self.fields()))
		order = self.save_order(user=SECOND_DRAFTER)

		with self.assertRaises(frappe.ValidationError):
			self.record(extraction, order.name)
		self.assertFalse(frappe.db.get_value(EXTRACTION, extraction, "created_doc"))

	def test_a_discarded_reading_cannot_be_recorded(self):
		extraction = self.read(extraction_reply(self.fields()))
		with as_user(DRAFT_USER):
			discard_extraction(extraction)
		order = self.save_order()

		with self.assertRaises(frappe.ValidationError):
			self.record(extraction, order.name)
		self.assertEqual(frappe.db.get_value(EXTRACTION, extraction, "status"), DISCARDED)

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

	def test_recording_the_same_document_again_only_reports(self):
		"""The save records the reading; the form's call after it asks how it went.

		So a second record of the same reading against the same document answers
		with what was recorded and changes nothing, stamps included.
		"""
		extraction = self.read(extraction_reply(self.fields(**{IBAN_FIELD: ALTERED_IBAN})))
		order = self.save_order(**{IBAN_FIELD: ALTERED_IBAN})
		first = self.record(extraction, order.name)
		stored = frappe.db.get_value(
			EXTRACTION, extraction, ["created_doc", "sensitive_flags", "modified"], as_dict=True
		)

		again = self.record(extraction, order.name)

		self.assertEqual(again, first)
		after = frappe.db.get_value(
			EXTRACTION, extraction, ["created_doc", "sensitive_flags", "modified"], as_dict=True
		)
		self.assertEqual(after, stored)

	def test_a_recorded_reading_cannot_be_tied_to_a_second_document(self):
		"""One reading, one document. Anything else would let a reading vouch twice."""
		extraction = self.read(extraction_reply(self.fields()))
		first = self.save_order()
		second = self.save_order()
		self.record(extraction, first.name)

		with self.assertRaises(frappe.ValidationError):
			self.record(extraction, second.name)

		self.assertEqual(frappe.db.get_value(EXTRACTION, extraction, "created_doc"), first.name)
		self.assertFalse(
			frappe.db.exists(
				"File",
				{
					"file_url": self.file.file_url,
					"attached_to_doctype": ORDER_DT,
					"attached_to_name": second.name,
				},
			)
		)

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


class TestFillHandoverRechecks(FillTestCase):
	"""A reading done this morning is not a permission held now.

	Each test reads the file, then changes something the reading depended on, and
	asks for the values again. Each starts with the control where it can: the
	same handover before the change, so a check that does nothing cannot pass.
	"""

	def set_property(self, doctype: str, fieldname: str, prop: str, value, prop_type: str) -> None:
		"""A Property Setter for the length of the test, removed and uncached after it."""
		make_property_setter(doctype, fieldname, prop, value, prop_type)
		self.forget_meta(doctype)
		self.addCleanup(self.drop_property, doctype, fieldname, prop)

	def drop_property(self, doctype: str, fieldname: str, prop: str) -> None:
		frappe.db.delete("Property Setter", {"doc_type": doctype, "field_name": fieldname, "property": prop})
		self.forget_meta(doctype)

	def forget_meta(self, doctype: str) -> None:
		frappe.clear_cache(doctype=doctype)
		# Masked fields are cached per doctype and user, apart from the meta.
		frappe.cache.delete_keys(f"masked_fields::{doctype}")

	def unmask_vendor_iban(self) -> None:
		"""Show the vendor's IBAN unmasked, so masking is not why a value is hidden."""
		if masking_supported():
			self.set_property(VENDOR_DT, IBAN_FIELD, "mask", 0, "Check")

	def restrict_vendors(self, to: str = VENDOR_ACME_HOLDINGS) -> None:
		"""Leave DRAFT_USER able to read one vendor only, from now on."""
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": DRAFT_USER,
				"allow": VENDOR_DT,
				"for_value": to,
				"apply_to_all_doctypes": 0,
				"applicable_for": VENDOR_DT,
			}
		).insert(ignore_permissions=True)
		frappe.clear_cache(user=DRAFT_USER)
		self.addCleanup(self.lift_vendor_restriction)

	def lift_vendor_restriction(self) -> None:
		frappe.db.delete("User Permission", {"user": DRAFT_USER, "allow": VENDOR_DT})
		frappe.clear_cache(user=DRAFT_USER)

	# --- links and candidates ---------------------------------------------------

	def test_a_link_out_of_reach_after_reading_goes_back_to_text(self):
		extraction = self.read(extraction_reply(self.fields(**{IBAN_FIELD: ALTERED_IBAN})))
		self.assertEqual(self.values(extraction)["values"]["vendor"], VENDOR_ACME)

		self.restrict_vendors()
		answer = self.values(extraction)

		self.assertNotIn("vendor", answer["values"])
		self.assertEqual(answer["candidates"]["vendor"]["options"], [])
		self.assertNotIn(VENDOR_ACME, frappe.as_json(answer["candidates"]))
		# The master went with it: nothing named, the mismatch kept, no shortcut.
		flag = answer["sensitive"][IBAN_FIELD]
		self.assertIsNone(flag["master_name"])
		self.assertIsNone(flag["master_value"])
		self.assertEqual(flag["mismatch"], 1)
		self.assertEqual(flag["blocked"], 1)

	def test_a_candidate_out_of_reach_after_reading_is_removed(self):
		extraction = self.read(extraction_reply(self.fields(vendor="FA Acme")))
		before = {option["name"] for option in self.values(extraction)["candidates"]["vendor"]["options"]}
		self.assertEqual(before, {VENDOR_ACME, VENDOR_ACME_HOLDINGS})

		self.restrict_vendors()
		after = {option["name"] for option in self.values(extraction)["candidates"]["vendor"]["options"]}

		self.assertEqual(after, {VENDOR_ACME_HOLDINGS})

	# --- duplicates -------------------------------------------------------------

	def test_a_duplicate_out_of_reach_after_reading_is_counted_not_named(self):
		first, _ = extract_as(DRAFT_USER, self.file.name, extraction_reply(self.fields()))
		extraction = self.read(extraction_reply(self.fields()))
		named = [row["name"] for row in self.values(extraction)["duplicates"]["same_file"]]
		self.assertIn(first.name, named)

		frappe.db.set_value(EXTRACTION, first.name, "owner", SECOND_DRAFTER, update_modified=False)
		duplicates = self.values(extraction)["duplicates"]

		self.assertNotIn(first.name, frappe.as_json(duplicates))
		self.assertEqual(duplicates["same_file_hidden_count"], 1)

	def test_a_saved_draft_does_not_collide_with_itself(self):
		order = make_order_draft(user=DRAFT_USER)
		started = self.start(docname=order.name)
		run_extraction_with(started["extraction"], extraction_reply(self.fields()))
		collision = {"fieldname": "order_title", "value": order.order_title, "name": order.name}
		frappe.db.set_value(
			EXTRACTION,
			started["extraction"],
			"duplicate_flags",
			frappe.as_json({"unique_collisions": [collision]}),
		)

		duplicates = self.values(started["extraction"])["duplicates"]

		self.assertEqual(duplicates["unique_collisions"], [])
		self.assertEqual(duplicates["unique_collisions_hidden_count"], 0)

	# --- fields and the target ----------------------------------------------------

	def test_a_field_made_read_only_after_reading_is_not_handed_over(self):
		extraction = self.read(extraction_reply(self.fields()))
		self.assertIn("amount", self.values(extraction)["values"])

		self.set_property(ORDER_DT, "amount", "read_only", 1, "Check")
		answer = self.values(extraction)

		self.assertNotIn("amount", answer["values"])
		self.assertIn("amount", answer["ignored"])

	def test_a_draft_submitted_after_reading_is_not_filled(self):
		order = make_order_draft(user=DRAFT_USER)
		started = self.start(docname=order.name)
		run_extraction_with(started["extraction"], extraction_reply(self.fields()))

		frappe.db.set_value(ORDER_DT, order.name, "docstatus", 1)
		with self.assertRaises(frappe.ValidationError) as caught:
			self.values(started["extraction"])
		self.assertIn("no longer a draft", str(caught.exception))

	# --- the master's value -------------------------------------------------------

	def test_the_master_value_shows_only_while_the_user_may_see_it(self):
		"""The control for the two tests below: unmasked, level 0, the value is shown."""
		self.unmask_vendor_iban()
		extraction = self.read(extraction_reply(self.fields(**{IBAN_FIELD: ALTERED_IBAN})))

		self.assertEqual(self.values(extraction)["sensitive"][IBAN_FIELD]["master_value"], VENDOR_IBAN)

	def test_a_master_field_above_the_users_permlevel_is_never_shown(self):
		"""The only field protection there is on version-15."""
		self.unmask_vendor_iban()
		self.set_property(VENDOR_DT, IBAN_FIELD, "permlevel", 1, "Int")
		extraction = self.read(extraction_reply(self.fields(**{IBAN_FIELD: ALTERED_IBAN})))

		stored = frappe.parse_json(frappe.db.get_value(EXTRACTION, extraction, "sensitive_flags"))[IBAN_FIELD]
		self.assertIsNone(stored["master_value"])
		self.assertEqual(stored["master_visible"], 0)
		self.assertEqual(stored["mismatch"], 1)

		flag = self.values(extraction)["sensitive"][IBAN_FIELD]
		self.assertIsNone(flag["master_value"])
		self.assertEqual(flag["mismatch"], 1)
		self.assertNotIn(VENDOR_IBAN, frappe.as_json(flag))

	def test_masking_turned_on_after_reading_hides_the_master_value(self):
		if not masking_supported():
			self.skipTest("this Frappe version has no field masking")
		self.unmask_vendor_iban()
		extraction = self.read(extraction_reply(self.fields(**{IBAN_FIELD: ALTERED_IBAN})))
		self.assertEqual(self.values(extraction)["sensitive"][IBAN_FIELD]["master_value"], VENDOR_IBAN)

		self.drop_property(VENDOR_DT, IBAN_FIELD, "mask")
		with as_user(DRAFT_USER):
			masked = {df.fieldname for df in frappe.get_meta(VENDOR_DT).get_masked_fields()}
		if IBAN_FIELD not in masked:
			# What this test checks is the handover's re-check, not the framework's
			# masking; where the framework does not report the mask there is nothing
			# for the re-check to act on.
			self.skipTest("the framework does not report this field as masked for this user")
		flag = self.values(extraction)["sensitive"][IBAN_FIELD]

		self.assertIsNone(flag["master_value"])
		self.assertEqual(flag["mismatch"], 1)

	def test_the_master_is_compared_again_at_handover(self):
		"""The one-click offer rests on the master as it is now, not this morning."""
		extraction = self.read(extraction_reply(self.fields(**{IBAN_FIELD: VENDOR_IBAN})))
		self.assertEqual(self.values(extraction)["sensitive"][IBAN_FIELD]["blocked"], 0)

		frappe.db.set_value(VENDOR_DT, VENDOR_ACME, IBAN_FIELD, ALTERED_IBAN)
		flag = self.values(extraction)["sensitive"][IBAN_FIELD]

		self.assertEqual(flag["mismatch"], 1)
		self.assertEqual(flag["blocked"], 1)

	def test_an_empty_master_is_not_agreement(self):
		extraction = self.read(
			extraction_reply(self.fields(vendor=VENDOR_ACME_HOLDINGS, **{IBAN_FIELD: ALTERED_IBAN}))
		)

		flag = self.values(extraction)["sensitive"][IBAN_FIELD]
		self.assertEqual(flag["master_name"], VENDOR_ACME_HOLDINGS)
		self.assertEqual(flag["compared"], 0)
		self.assertEqual(flag["mismatch"], 0)
		self.assertEqual(flag["blocked"], 1)


class TestFillRecordedBySave(FillTestCase):
	"""The save records the reading. The browser's call after it is only a courtesy."""

	def save_with_token(self, extraction: str, user: str = DRAFT_USER, **values):
		order = frappe.get_doc(
			{
				"doctype": ORDER_DT,
				"order_title": f"FA Saved {frappe.generate_hash(length=8)}",
				"project": PROJECT_ALPHA,
				"vendor": VENDOR_ACME,
				TOKEN_KEY: extraction,
				**values,
			}
		)
		with as_user(user):
			order.insert()
		return order

	def test_the_save_itself_records_the_reading(self):
		extraction = self.read(extraction_reply(self.fields(**{IBAN_FIELD: ALTERED_IBAN})))

		order = self.save_with_token(extraction, **{IBAN_FIELD: ALTERED_IBAN})

		doc = frappe.get_doc(EXTRACTION, extraction)
		self.assertEqual(doc.status, ACCEPTED)
		self.assertEqual(doc.created_doc, order.name)
		self.assertEqual(frappe.parse_json(doc.sensitive_flags)[IBAN_FIELD]["saved"], "printed")
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

	def test_the_call_after_the_save_reports_what_the_save_recorded(self):
		extraction = self.read(extraction_reply(self.fields(**{IBAN_FIELD: ALTERED_IBAN})))
		order = self.save_with_token(extraction)

		with as_user(DRAFT_USER):
			done = record_filled_document(extraction, order.name)

		self.assertEqual(done["status"], ACCEPTED)
		self.assertEqual(done["created_doc"], order.name)
		self.assertTrue(done["source_attached"])
		self.assertEqual(done["sensitive"][IBAN_FIELD], "empty")

	def test_a_token_for_someone_elses_reading_changes_nothing(self):
		extraction = self.read(extraction_reply(self.fields()))

		order = self.save_with_token(extraction, user=SECOND_DRAFTER)

		self.assertTrue(frappe.db.exists(ORDER_DT, order.name))
		self.assertEqual(frappe.db.get_value(EXTRACTION, extraction, "status"), READY)

	def test_a_token_that_names_nothing_never_breaks_the_save(self):
		order = self.save_with_token("FA no such reading")

		self.assertTrue(frappe.db.exists(ORDER_DT, order.name))
