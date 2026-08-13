# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The schema handed to the model, and the fields that never appear in it.

Two separate promises live here.

The first is portability: every property required, no unions, no nulls, no
numeric or length constraints, `additionalProperties: false` everywhere. Both
provider families reject the obvious shape for different reasons, and a schema
that 400s at the provider is an extraction that never happens.

The second is the permission boundary, and it is the one that matters. A field
the requesting user may not write is not filtered out of the answer — it never
reaches the model at all. A field that was never asked for cannot come back.
"""

import frappe

from frappe_agents.extraction.schema import build_extraction_schema, normalise_values
from frappe_agents.tests.fixtures import (
	DRAFT_USER,
	IBAN_FIELD,
	ORDER_DT,
	ORDER_ITEM_DT,
	PROJECT_ALPHA,
	RESTRICTED_USER,
	VENDOR_ACME,
	AgentTestCase,
	extract_as,
	extraction_json,
	extraction_reply,
	make_pdf_attachment,
)

# Keys Anthropic rejects outright, so they may not appear anywhere in the schema.
FORBIDDEN_KEYWORDS = ("anyOf", "oneOf", "allOf", "not", "minimum", "maximum", "minLength", "maxLength")


class TestExtractionSchema(AgentTestCase):
	def spec(self, user: str = DRAFT_USER) -> dict:
		return build_extraction_schema(ORDER_DT, user)

	def properties(self, user: str = DRAFT_USER) -> dict:
		return self.spec(user)["schema"]["properties"]["fields"]["properties"]

	def objects(self, node) -> list[dict]:
		"""Every object node in the schema, root and rows alike."""
		found = []
		if isinstance(node, dict):
			if node.get("type") == "object":
				found.append(node)
			for value in node.values():
				found += self.objects(value)
		elif isinstance(node, list):
			for value in node:
				found += self.objects(value)
		return found

	# --- the shape both providers accept -------------------------------------

	def test_every_property_is_required_on_every_object(self):
		"""OpenAI strict mode demands it, and it takes Anthropic's optional count to zero."""
		for obj in self.objects(self.spec()["schema"]):
			self.assertEqual(sorted(obj.get("properties") or {}), sorted(obj.get("required") or []))

	def test_no_object_accepts_a_property_we_did_not_ask_for(self):
		for obj in self.objects(self.spec()["schema"]):
			self.assertFalse(obj.get("additionalProperties"))

	def test_the_schema_carries_no_unions_and_no_constraints(self):
		"""Absence is said with a sentinel, never with null or a union."""
		text = frappe.as_json(self.spec()["schema"])
		for keyword in FORBIDDEN_KEYWORDS:
			self.assertNotIn(f'"{keyword}"', text)
		self.assertNotIn('"null"', text)

	def test_the_root_asks_for_the_values_and_the_models_own_notes(self):
		schema = self.spec()["schema"]
		self.assertEqual(sorted(schema["required"]), ["field_notes", "fields"])

		notes = schema["properties"]["field_notes"]
		self.assertEqual(notes["type"], "array")
		self.assertEqual(sorted(notes["items"]["required"]), ["confidence", "fieldname", "page"])

	def test_a_select_offers_its_options_and_a_way_to_say_nothing_was_printed(self):
		payment_terms = self.properties()["payment_terms"]
		self.assertEqual(payment_terms["type"], "string")
		self.assertIn("Net 30", payment_terms["enum"])
		self.assertIn("", payment_terms["enum"])

	def test_a_select_value_comes_back_matched_case_insensitively(self):
		"""No provider guarantees the casing of an enum it echoes back."""
		values = normalise_values(self.spec(), {"fields": {"payment_terms": "net 30"}})

		self.assertEqual(values["payment_terms"], "Net 30")

	def test_a_child_table_is_asked_for_as_rows(self):
		spec = self.spec()
		items = spec["schema"]["properties"]["fields"]["properties"]["items"]

		self.assertEqual(items["type"], "array")
		self.assertIn("item", items["items"]["properties"])
		self.assertEqual(spec["child_tables"]["items"]["doctype"], ORDER_ITEM_DT)

	# --- what never reaches the model ----------------------------------------

	def test_a_field_above_the_users_permlevel_is_absent_from_the_schema(self):
		properties = self.properties()

		self.assertNotIn("internal_rate", properties)
		self.assertIn("order_title", properties)

	def test_a_child_field_above_the_users_permlevel_is_absent_too(self):
		items = self.spec()["schema"]["properties"]["fields"]["properties"]["items"]

		self.assertNotIn("unit_cost", items["items"]["properties"])
		self.assertIn("qty", items["items"]["properties"])

	def test_the_frameworks_own_fields_are_never_asked_for(self):
		properties = self.properties()

		for fieldname in ("name", "owner", "docstatus", "idx", "amended_from", "modified"):
			self.assertNotIn(fieldname, properties)

	def test_a_sensitive_field_is_asked_for_and_marked(self):
		"""The gate cannot flag what the model was never asked to read."""
		spec = self.spec()

		self.assertIn(IBAN_FIELD, spec["schema"]["properties"]["fields"]["properties"])
		self.assertIn(IBAN_FIELD, spec["sensitive"])
		self.assertIn(IBAN_FIELD, spec["child_tables"]["items"]["sensitive"])

	def test_the_schema_the_provider_receives_is_the_one_with_the_holes_in_it(self):
		"""The schema is built per user, so assert on what actually crossed the wire."""
		file = make_pdf_attachment()
		reply = extraction_reply({"order_title": "FA Extracted schema", "project": PROJECT_ALPHA})
		_, call = extract_as(DRAFT_USER, file.name, reply)

		schema = call.call_args.args[4]
		properties = schema["properties"]["fields"]["properties"]
		self.assertNotIn("internal_rate", properties)
		self.assertIn(IBAN_FIELD, properties)

	def test_a_field_the_model_returns_anyway_is_dropped(self):
		"""Defence in depth: a router that treats the schema as a hint gets nowhere."""
		file = make_pdf_attachment()
		reply = extraction_reply(
			{
				"order_title": f"FA Extracted {frappe.generate_hash(length=8)}",
				"project": PROJECT_ALPHA,
				"vendor": VENDOR_ACME,
				"internal_rate": "rate-invented-by-the-model",
				"docstatus": 1,
				"owner": "Administrator",
			}
		)
		doc, _ = extract_as(DRAFT_USER, file.name, reply)

		values = extraction_json(doc, "extracted_json")
		self.assertNotIn("internal_rate", values)
		self.assertNotIn("docstatus", values)
		self.assertNotIn("owner", values)

		draft = frappe.get_doc(ORDER_DT, doc.created_doc)
		self.assertEqual(draft.docstatus, 0)
		self.assertEqual(draft.owner, DRAFT_USER)
		self.assertFalse(draft.internal_rate)

	# --- when there is nothing to ask for ------------------------------------

	def test_a_user_who_may_write_nothing_gets_a_refusal_in_words(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self.spec(RESTRICTED_USER)

		self.assertIn(ORDER_DT, str(caught.exception))

	def test_a_child_table_is_not_an_extraction_target(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			build_extraction_schema(ORDER_ITEM_DT, DRAFT_USER)

		self.assertIn("child table", str(caught.exception))

	def test_a_single_is_not_an_extraction_target(self):
		with self.assertRaises(frappe.ValidationError):
			build_extraction_schema("Agent Settings", "Administrator")
