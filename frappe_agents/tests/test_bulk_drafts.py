# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Many drafts in one call, and the two things that keeps.

A spreadsheet read in chat is one record per row, and asking for them one call at
a time is how a forty-row sheet becomes forty turns. `create_drafts` is the same
insert repeated, so these tests are about what repeating it must not change: the
create check still decides the whole batch before a single row is written, and no
row can be created that `create_draft` would have refused.

The other half is failure. A batch is a queue for a human to work through, so a
row the database rejects has to cost that row alone — including when it is
rejected by SQL, which leaves the transaction unusable until something rolls it
back. The duplicate test below is the one that proves the rest of the batch
survives that.
"""

import frappe

from frappe_agents.tests.fixtures import (
	DRAFT_USER,
	ORDER_DT,
	PROJECT_ALPHA,
	RESTRICTED_USER,
	AgentTestCase,
	call_tool,
	tool_calls_for,
)
from frappe_agents.tools.draft_tools import BULK_ROW_LIMIT


class TestBulkDrafts(AgentTestCase):
	def title(self) -> str:
		return f"FA Bulk {frappe.generate_hash(length=8)}"

	def row(self, title: str | None = None, **extra) -> dict:
		return {"order_title": title or self.title(), "project": PROJECT_ALPHA, "amount": 50, **extra}

	def test_every_row_becomes_a_draft_owned_by_the_effective_user(self):
		rows = [self.row(), self.row(), self.row()]

		payload, _ = call_tool(DRAFT_USER, "create_drafts", {"doctype": ORDER_DT, "rows": rows})

		self.assertTrue(payload["ok"], payload["error"])
		result = payload["result"]
		self.assertEqual(result["succeeded"], 3)
		self.assertEqual(result["failed"], 0)
		self.assertEqual(result["created"], [row["order_title"] for row in rows])
		self.assertEqual([outcome["index"] for outcome in result["outcomes"]], [0, 1, 2])

		for row in rows:
			order = frappe.get_doc(ORDER_DT, row["order_title"])
			self.assertEqual(order.docstatus, 0)
			self.assertEqual(order.owner, DRAFT_USER)
			self.assertEqual(order.amount, 50)

	def test_a_row_that_fails_does_not_take_the_batch_with_it(self):
		good, bad, after = self.row(), {"amount": 50}, self.row()

		payload, _ = call_tool(DRAFT_USER, "create_drafts", {"doctype": ORDER_DT, "rows": [good, bad, after]})

		self.assertTrue(payload["ok"], payload["error"])
		result = payload["result"]
		self.assertEqual(result["succeeded"], 2)
		self.assertEqual(result["failed"], 1)
		self.assertEqual(result["outcomes"][0]["name"], good["order_title"])
		self.assertTrue(result["outcomes"][1]["error"])
		self.assertEqual(result["outcomes"][1]["index"], 1)
		self.assertEqual(result["outcomes"][2]["name"], after["order_title"])

		self.assertTrue(frappe.db.exists(ORDER_DT, good["order_title"]))
		self.assertTrue(frappe.db.exists(ORDER_DT, after["order_title"]))

	def test_a_row_the_database_rejects_leaves_the_rest_of_the_batch_writable(self):
		"""A duplicate name fails in SQL, and SQL is what a savepoint is for."""
		taken = self.title()
		after = self.row()

		payload, _ = call_tool(
			DRAFT_USER,
			"create_drafts",
			{"doctype": ORDER_DT, "rows": [self.row(taken), self.row(taken), after]},
		)

		self.assertTrue(payload["ok"], payload["error"])
		result = payload["result"]
		self.assertEqual(result["created"], [taken, after["order_title"]])
		self.assertTrue(result["outcomes"][1]["error"])
		self.assertTrue(frappe.db.exists(ORDER_DT, after["order_title"]))
		self.assertEqual(frappe.db.count(ORDER_DT, {"order_title": taken}), 1)

	def test_a_doctype_the_user_may_not_create_is_refused_before_any_insert(self):
		"""The restricted user may read orders and nothing else."""
		rows = [self.row(), self.row()]
		before = frappe.db.count(ORDER_DT)

		payload, run = call_tool(RESTRICTED_USER, "create_drafts", {"doctype": ORDER_DT, "rows": rows})

		self.assertFalse(payload["ok"])
		self.assertIn(ORDER_DT, payload["error"])
		self.assertEqual(frappe.db.count(ORDER_DT), before)
		for row in rows:
			self.assertFalse(frappe.db.exists(ORDER_DT, row["order_title"]))

		calls = tool_calls_for(run.name)
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].outcome, "Denied")
		self.assertEqual(calls[0].tool, "create_drafts")

	def test_past_the_row_ceiling_the_whole_call_is_refused(self):
		rows = [self.row() for _ in range(BULK_ROW_LIMIT + 1)]
		before = frappe.db.count(ORDER_DT)

		payload, _ = call_tool(DRAFT_USER, "create_drafts", {"doctype": ORDER_DT, "rows": rows})

		self.assertFalse(payload["ok"])
		self.assertIn(str(BULK_ROW_LIMIT), payload["error"])
		self.assertEqual(frappe.db.count(ORDER_DT), before)

	def test_the_ceiling_itself_is_allowed(self):
		"""The limit is a limit, not an off-by-one refusal of the last row."""
		rows = [self.row() for _ in range(BULK_ROW_LIMIT)]

		payload, _ = call_tool(DRAFT_USER, "create_drafts", {"doctype": ORDER_DT, "rows": rows})

		self.assertTrue(payload["ok"], payload["error"])
		self.assertEqual(payload["result"]["succeeded"], BULK_ROW_LIMIT)

	def test_dry_run_writes_nothing_and_still_says_which_rows_would_fail(self):
		good, bad = self.row(), {"amount": 50}
		before = frappe.db.count(ORDER_DT)

		payload, _ = call_tool(
			DRAFT_USER,
			"create_drafts",
			{"doctype": ORDER_DT, "rows": [good, bad], "dry_run": True},
		)

		self.assertTrue(payload["ok"], payload["error"])
		result = payload["result"]
		self.assertTrue(result["dry_run"])
		self.assertEqual(result["created"], [])
		self.assertTrue(result["outcomes"][0]["would_insert"])
		self.assertFalse(result["outcomes"][1]["would_insert"])
		self.assertTrue(result["outcomes"][1]["error"])

		self.assertEqual(frappe.db.count(ORDER_DT), before)
		self.assertFalse(frappe.db.exists(ORDER_DT, good["order_title"]))

	def test_the_audit_row_names_every_created_draft(self):
		rows = [self.row(), self.row(), self.row()]

		_, run = call_tool(DRAFT_USER, "create_drafts", {"doctype": ORDER_DT, "rows": rows})

		calls = tool_calls_for(run.name)
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].outcome, "Success")
		for row in rows:
			self.assertIn(row["order_title"], calls[0].docs_touched)

	def test_a_row_cannot_carry_a_docstatus_past_the_same_strip(self):
		"""Bulk is the single insert repeated, so the same values are dropped."""
		title = self.title()

		payload, _ = call_tool(
			DRAFT_USER,
			"create_drafts",
			{"doctype": ORDER_DT, "rows": [self.row(title, docstatus=1, owner="Administrator")]},
		)

		self.assertTrue(payload["ok"], payload["error"])
		self.assertIn("docstatus", payload["result"]["stripped_keys"])
		self.assertIn("owner", payload["result"]["stripped_keys"])

		order = frappe.get_doc(ORDER_DT, title)
		self.assertEqual(order.docstatus, 0)
		self.assertEqual(order.owner, DRAFT_USER)

	def test_an_empty_batch_is_refused_in_words(self):
		payload, _ = call_tool(DRAFT_USER, "create_drafts", {"doctype": ORDER_DT, "rows": []})

		self.assertFalse(payload["ok"])
		self.assertIn("rows", payload["error"])
