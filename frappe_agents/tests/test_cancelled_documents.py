# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Cancelled documents, which are the ones that make an agent lie.

A cancelled document is a document that was undone. Counting it, or quoting a
number off it, states something that stopped being true — so it is left out
unless the question was about what was cancelled. The document is not hidden:
it comes back on request, labelled CANCELLED, with the amendment that replaced
it reachable from its manifest.
"""

from frappe_agents.context.manifest import build_manifest
from frappe_agents.tests.fixtures import (
	ORDER_CANCELLED,
	ORDER_DT,
	ORDER_LIVE,
	RESTRICTED_USER,
	AgentTestCase,
	amended_order,
	as_user,
	make_run,
)
from frappe_agents.tools.base import execute_tool
from frappe_agents.tools.read_tools import search_documents


class TestCancelledDocuments(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.amendment = amended_order()
		self.assertTrue(self.amendment, "the cancelled order has no amendment")

	def search(self, **args) -> dict:
		# Scoped to this test's own family: other tests' fixtures ride implicit
		# DDL commits and survive rollback, so an unscoped exact-set assertion
		# is order-dependent — it fails whenever the suite runs in a new order.
		family = ["in", [ORDER_LIVE, ORDER_CANCELLED, self.amendment]]
		with as_user(RESTRICTED_USER):
			return search_documents(
				{
					"doctype": ORDER_DT,
					"fields": ["name", "order_title"],
					"filters": {"name": family},
					**args,
				}
			)

	def names(self, result: dict) -> set:
		return {row["name"] for row in result["rows"]}

	def row_for(self, result: dict, name: str) -> dict:
		for row in result["rows"]:
			if row["name"] == name:
				return row
		self.fail(f"{name} not in {self.names(result)}")

	def test_a_cancelled_document_is_left_out_by_default(self):
		result = self.search()

		self.assertEqual(self.names(result), {ORDER_LIVE, self.amendment})
		self.assertNotIn(ORDER_CANCELLED, self.names(result))
		self.assertFalse(result["include_cancelled"])

	def test_include_cancelled_returns_it_labelled(self):
		result = self.search(include_cancelled=True)

		self.assertEqual(self.names(result), {ORDER_LIVE, ORDER_CANCELLED, self.amendment})
		self.assertTrue(result["include_cancelled"])
		self.assertEqual(self.row_for(result, ORDER_CANCELLED)["status_word"], "CANCELLED")
		self.assertEqual(self.row_for(result, ORDER_LIVE)["status_word"], "SUBMITTED")
		self.assertEqual(self.row_for(result, self.amendment)["status_word"], "DRAFT")

	def test_rows_carry_a_word_and_never_the_integer(self):
		result = self.search(include_cancelled=True)

		for row in result["rows"]:
			self.assertNotIn("docstatus", row)
			self.assertIn(row["status_word"], ("DRAFT", "SUBMITTED", "CANCELLED"))
		self.assertIn("status_word", result["fields"])
		self.assertNotIn("docstatus", result["fields"])

	def test_an_explicit_docstatus_filter_wins(self):
		"""Asking for cancelled documents by filter is asking for them."""
		result = self.search(filters={"docstatus": 2})

		self.assertEqual(self.names(result), {ORDER_CANCELLED})
		self.assertFalse(result["include_cancelled"])
		self.assertEqual(result["rows"][0]["status_word"], "CANCELLED")

	def test_every_result_reports_what_permission_dropped(self):
		"""filtered_by_permission is always on the result, so nobody has to guess it was zero."""
		for result in (self.search(), self.search(include_cancelled=True)):
			self.assertIn("filtered_by_permission", result)
			self.assertEqual(result["filtered_by_permission"], 0)

	def test_the_manifest_of_a_cancelled_document_points_at_its_amendment(self):
		with as_user(RESTRICTED_USER):
			manifest = build_manifest(ORDER_DT, ORDER_CANCELLED)

		self.assertEqual(manifest["doc"]["status_word"], "CANCELLED")
		self.assertEqual(manifest["doc"]["amended_by"], self.amendment)
		self.assertNotIn("docstatus", manifest["doc"])

	def test_the_manifest_of_an_amendment_points_back(self):
		with as_user(RESTRICTED_USER):
			manifest = build_manifest(ORDER_DT, self.amendment)

		self.assertEqual(manifest["doc"]["status_word"], "DRAFT")
		self.assertEqual(manifest["doc"]["amended_from"], ORDER_CANCELLED)

	def test_the_exclusion_holds_through_the_tool_layer(self):
		run = make_run(effective_user=RESTRICTED_USER)

		with as_user(RESTRICTED_USER):
			payload = execute_tool(run, "search_documents", {"doctype": ORDER_DT, "fields": ["name"]})

		self.assertTrue(payload["ok"], payload["error"])
		# An exact match: the amendment's name has the cancelled one as a prefix.
		self.assertNotIn(ORDER_CANCELLED, {row["name"] for row in payload["result"]["rows"]})
