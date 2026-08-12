# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The context assembler, hop by hop.

The manifest and the slices answer two different questions — "what is attached to
this document" and "show me one part of it" — and both answer them for one
specific user. A document the user may not read is a number and a doctype name.
It is never a name, a title, or a field value.
"""

import frappe

from frappe_agents.context.manifest import build_manifest
from frappe_agents.context.slices import RESTRICTED, get_slice
from frappe_agents.tests.fixtures import (
	OPEN_USER,
	ORDER_DT,
	ORDER_ITEM_DT,
	ORDER_LIVE,
	ORDER_RATE,
	ORDER_UNIT_COST,
	RESTRICTED_USER,
	TICKET_ALPHA,
	TICKET_BETA,
	TICKET_DT,
	VAULT_DT,
	VAULT_RECORD,
	AgentTestCase,
	as_user,
	make_run,
	tool_calls_for,
)
from frappe_agents.tools.base import ToolDenied, execute_tool


class TestDocumentContext(AgentTestCase):
	def manifest_as(self, user: str, doctype: str, name: str) -> dict:
		with as_user(user):
			return build_manifest(doctype, name)

	def slice_as(self, user: str, doctype: str, name: str, slice_name: str, **params) -> dict:
		with as_user(user):
			return get_slice(doctype, name, slice_name, **params)

	def group_for(self, groups: list, doctype: str) -> dict:
		for group in groups:
			if group["doctype"] == doctype:
				return group
		self.fail(f"{doctype} not in {[group['doctype'] for group in groups]}")

	def test_the_focal_document_gate_refuses_an_unreadable_document(self):
		"""Everything a slice returns hangs off read permission on the focal document."""
		with as_user(RESTRICTED_USER):
			self.assertRaises(ToolDenied, build_manifest, TICKET_DT, TICKET_BETA)
			self.assertRaises(ToolDenied, build_manifest, VAULT_DT, VAULT_RECORD)
			self.assertRaises(ToolDenied, get_slice, VAULT_DT, VAULT_RECORD, "fields")

	def test_an_unreadable_neighbour_is_counted_not_named(self):
		"""The ticket is readable. The vault record pointing at it is not."""
		manifest = self.manifest_as(RESTRICTED_USER, TICKET_DT, TICKET_ALPHA)

		links = manifest["slices"]["links"]
		self.assertIn(VAULT_DT, links)
		self.assertEqual(links[VAULT_DT]["visible_count"], 0)
		self.assertEqual(links[VAULT_DT]["invisible_count"], 1)
		self.assertIn(VAULT_DT, manifest["not_visible"]["doctypes"])
		self.assertGreaterEqual(manifest["not_visible"]["count"], 1)
		self.assertNotIn(VAULT_RECORD, frappe.as_json(manifest))

	def test_the_links_slice_omits_the_unreadable_neighbour(self):
		payload = self.slice_as(RESTRICTED_USER, TICKET_DT, TICKET_ALPHA, "links", direction="down")

		group = self.group_for(payload["down"], VAULT_DT)
		self.assertEqual(group["docs"], [])
		self.assertEqual(group["visible_count"], 0)
		self.assertEqual(group["invisible_count"], 1)
		self.assertIn(VAULT_DT, payload["not_visible"]["doctypes"])
		self.assertNotIn(VAULT_RECORD, frappe.as_json(payload))

	def test_the_links_slice_returns_the_neighbour_to_someone_who_may_read_it(self):
		"""The positive control: the row is hidden by permission, not by the traversal."""
		payload = self.slice_as("Administrator", TICKET_DT, TICKET_ALPHA, "links", direction="down")

		group = self.group_for(payload["down"], VAULT_DT)
		self.assertEqual([doc["name"] for doc in group["docs"]], [VAULT_RECORD])
		self.assertEqual(group["invisible_count"], 0)

	def test_the_manifest_marks_a_field_above_the_users_permlevel(self):
		manifest = self.manifest_as(RESTRICTED_USER, ORDER_DT, ORDER_LIVE)
		self.assertEqual(manifest["doc"]["internal_rate"], RESTRICTED)
		self.assertIn("internal_rate", manifest["doc"]["restricted_fields"])
		self.assertNotIn(ORDER_RATE, frappe.as_json(manifest))

		manifest = self.manifest_as(OPEN_USER, ORDER_DT, ORDER_LIVE)
		self.assertEqual(manifest["doc"]["internal_rate"], ORDER_RATE)
		self.assertNotIn("restricted_fields", manifest["doc"])

	def test_the_fields_slice_marks_a_field_above_the_users_permlevel(self):
		payload = self.slice_as(RESTRICTED_USER, ORDER_DT, ORDER_LIVE, "fields")
		self.assertEqual(payload["fields"]["internal_rate"], RESTRICTED)
		self.assertEqual(payload["restricted_fields"], ["internal_rate"])
		self.assertNotIn(ORDER_RATE, frappe.as_json(payload))

		payload = self.slice_as(OPEN_USER, ORDER_DT, ORDER_LIVE, "fields")
		self.assertEqual(payload["fields"]["internal_rate"], ORDER_RATE)
		self.assertEqual(payload["restricted_fields"], [])

	def test_a_child_row_carries_the_parents_permlevel(self):
		"""A child table has no permissions of its own: the parent's rules decide."""
		payload = self.slice_as(RESTRICTED_USER, ORDER_DT, ORDER_LIVE, "child_table", table="items")
		self.assertEqual(payload["child_doctype"], ORDER_ITEM_DT)
		self.assertEqual(payload["row_count"], 1)
		self.assertEqual(payload["rows"][0]["item"], "FA Widget")
		self.assertEqual(payload["rows"][0]["unit_cost"], RESTRICTED)
		self.assertIn("unit_cost", payload["restricted_fields"])
		self.assertNotIn(ORDER_UNIT_COST, frappe.as_json(payload))

		payload = self.slice_as(OPEN_USER, ORDER_DT, ORDER_LIVE, "child_table", table="items")
		self.assertEqual(payload["rows"][0]["unit_cost"], ORDER_UNIT_COST)
		self.assertEqual(payload["restricted_fields"], [])

	def test_the_manifest_counts_child_rows_without_returning_them(self):
		manifest = self.manifest_as(RESTRICTED_USER, ORDER_DT, ORDER_LIVE)

		self.assertEqual(
			manifest["slices"]["child_tables"]["items"],
			{"child_doctype": ORDER_ITEM_DT, "rows": 1},
		)
		self.assertNotIn("FA Widget", frappe.as_json(manifest))

	def test_the_manifest_never_returns_a_docstatus_integer(self):
		manifest = self.manifest_as(RESTRICTED_USER, ORDER_DT, ORDER_LIVE)
		self.assertEqual(manifest["doc"]["status_word"], "SUBMITTED")
		self.assertNotIn("docstatus", manifest["doc"])

	def test_an_unknown_slice_is_rejected(self):
		with as_user(RESTRICTED_USER):
			self.assertRaises(ValueError, get_slice, ORDER_DT, ORDER_LIVE, "everything")

	def test_both_tools_run_through_the_tool_layer(self):
		"""The agent has to be granted the tools, or every call is a denial."""
		run = make_run(effective_user=RESTRICTED_USER)

		with as_user(RESTRICTED_USER):
			manifest = execute_tool(run, "get_document_context", {"doctype": ORDER_DT, "name": ORDER_LIVE})
			fields = execute_tool(
				run,
				"get_document_slice",
				{"doctype": ORDER_DT, "name": ORDER_LIVE, "slice": "fields"},
			)

		self.assertTrue(manifest["ok"], manifest["error"])
		self.assertTrue(fields["ok"], fields["error"])
		self.assertEqual(manifest["result"]["name"], ORDER_LIVE)
		self.assertEqual(fields["result"]["slice"], "fields")

		calls = tool_calls_for(run.name)
		self.assertEqual([call.outcome for call in calls], ["Success", "Success"])
		for call in calls:
			self.assertIn(ORDER_LIVE, call.docs_touched)

	def test_a_denied_document_is_denied_through_the_tool_layer_too(self):
		run = make_run(effective_user=RESTRICTED_USER)

		with as_user(RESTRICTED_USER):
			payload = execute_tool(run, "get_document_context", {"doctype": VAULT_DT, "name": VAULT_RECORD})

		self.assertFalse(payload["ok"])
		self.assertIsNone(payload["result"])
		calls = tool_calls_for(run.name)
		self.assertEqual([call.outcome for call in calls], ["Denied"])
