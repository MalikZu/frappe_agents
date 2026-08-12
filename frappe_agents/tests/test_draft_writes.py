# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The agent's own workspace: drafts.

A draft commits the business to nothing, so an agent writes one directly — but
only through the effective user's own permissions, and only as a draft. The two
things these tests pin down are that the permission model is the boundary (not a
rule inside the tool), and that no value in a payload can carry a document across
the docstatus line.
"""

import frappe

from frappe_agents.tests.fixtures import (
	AGENT,
	DRAFT_USER,
	ORDER_CANCELLED,
	ORDER_DT,
	ORDER_LIVE,
	PROJECT_ALPHA,
	RESTRICTED_USER,
	SECOND_DRAFTER,
	AgentTestCase,
	call_tool,
	make_order_draft,
	make_run,
	tool_calls_for,
)


class TestDraftWrites(AgentTestCase):
	def values(self, title: str, **extra) -> dict:
		return {"order_title": title, "project": PROJECT_ALPHA, "amount": 50, **extra}

	def title(self) -> str:
		return f"FA Draft {frappe.generate_hash(length=8)}"

	def test_create_draft_without_create_permission_is_denied_and_logged(self):
		"""The restricted user may read orders and nothing else."""
		title = self.title()

		payload, run = call_tool(
			RESTRICTED_USER, "create_draft", {"doctype": ORDER_DT, "values": self.values(title)}
		)

		self.assertFalse(payload["ok"])
		self.assertIn(ORDER_DT, payload["error"])
		self.assertFalse(frappe.db.exists(ORDER_DT, title))

		calls = tool_calls_for(run.name)
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].outcome, "Denied")
		self.assertEqual(calls[0].tool, "create_draft")

	def test_create_draft_writes_a_draft_owned_by_the_effective_user(self):
		title = self.title()

		payload, run = call_tool(
			DRAFT_USER, "create_draft", {"doctype": ORDER_DT, "values": self.values(title)}
		)

		self.assertTrue(payload["ok"], payload["error"])
		self.assertEqual(payload["result"]["name"], title)
		self.assertEqual(payload["result"]["status_word"], "DRAFT")

		order = frappe.get_doc(ORDER_DT, title)
		self.assertEqual(order.docstatus, 0)
		self.assertEqual(order.owner, DRAFT_USER)
		self.assertEqual(order.amount, 50)

		calls = tool_calls_for(run.name)
		self.assertEqual(calls[0].outcome, "Success")
		self.assertIn(title, calls[0].docs_touched)

	def test_a_docstatus_in_the_payload_is_stripped_and_the_document_stays_a_draft(self):
		"""The one value an agent must never be able to set."""
		title = self.title()

		payload, _ = call_tool(
			DRAFT_USER,
			"create_draft",
			{
				"doctype": ORDER_DT,
				"values": self.values(title, docstatus=1, owner="Administrator", modified="2020-01-01"),
			},
		)

		self.assertTrue(payload["ok"], payload["error"])
		stripped = payload["result"]["stripped_keys"]
		self.assertIn("docstatus", stripped)
		self.assertIn("owner", stripped)
		self.assertIn("modified", stripped)

		order = frappe.get_doc(ORDER_DT, title)
		self.assertEqual(order.docstatus, 0)
		self.assertEqual(order.owner, DRAFT_USER)

	def test_a_child_row_cannot_carry_its_own_parent_linkage(self):
		title = self.title()
		rows = [{"item": "FA Widget", "qty": 1, "parent": ORDER_LIVE, "parenttype": ORDER_DT}]

		payload, _ = call_tool(
			DRAFT_USER,
			"create_draft",
			{"doctype": ORDER_DT, "values": self.values(title, items=rows)},
		)

		self.assertTrue(payload["ok"], payload["error"])
		self.assertIn("items[0].parent", payload["result"]["stripped_keys"])
		self.assertIn("items[0].parenttype", payload["result"]["stripped_keys"])

		order = frappe.get_doc(ORDER_DT, title)
		self.assertEqual(len(order.items), 1)
		self.assertEqual(order.items[0].parent, title)

	def test_dry_run_writes_nothing(self):
		title = self.title()
		before = frappe.db.count(ORDER_DT)

		payload, _ = call_tool(
			DRAFT_USER,
			"create_draft",
			{"doctype": ORDER_DT, "values": self.values(title), "dry_run": True},
		)

		self.assertTrue(payload["ok"], payload["error"])
		self.assertTrue(payload["result"]["dry_run"])
		self.assertTrue(payload["result"]["would_insert"])
		self.assertFalse(frappe.db.exists(ORDER_DT, title))
		self.assertEqual(frappe.db.count(ORDER_DT), before)

	def test_dry_run_reports_a_validation_failure_as_an_answer(self):
		"""A document that would be rejected comes back as text, not as an error."""
		payload, _ = call_tool(
			DRAFT_USER,
			"create_draft",
			{"doctype": ORDER_DT, "values": {"amount": 50}, "dry_run": True},
		)

		self.assertTrue(payload["ok"], payload["error"])
		self.assertFalse(payload["result"]["would_insert"])
		self.assertTrue(payload["result"]["validation_error"])

	def test_update_draft_refuses_a_submitted_document_in_words(self):
		payload, _ = call_tool(
			DRAFT_USER,
			"update_draft",
			{"doctype": ORDER_DT, "name": ORDER_LIVE, "values": {"amount": 999}},
		)

		self.assertFalse(payload["ok"])
		self.assertIn("not a draft", payload["error"])
		self.assertIn("propose_cancel", payload["error"])
		self.assertEqual(frappe.db.get_value(ORDER_DT, ORDER_LIVE, "amount"), 100)

	def test_update_draft_refuses_a_cancelled_document(self):
		payload, _ = call_tool(
			DRAFT_USER,
			"update_draft",
			{"doctype": ORDER_DT, "name": ORDER_CANCELLED, "values": {"amount": 999}},
		)

		self.assertFalse(payload["ok"])
		self.assertIn("cancelled", payload["error"])

	def test_update_draft_on_another_users_draft_is_allowed_when_they_may_write_it(self):
		"""The permission model is the boundary. Ownership is not a second one."""
		order = make_order_draft(user=DRAFT_USER)

		payload, _ = call_tool(
			SECOND_DRAFTER,
			"update_draft",
			{"doctype": ORDER_DT, "name": order.name, "values": {"amount": 250}},
		)

		self.assertTrue(payload["ok"], payload["error"])
		self.assertEqual(payload["result"]["updated_fields"], ["amount"])
		self.assertEqual(frappe.db.get_value(ORDER_DT, order.name, "amount"), 250)
		self.assertEqual(frappe.db.get_value(ORDER_DT, order.name, "owner"), DRAFT_USER)

	def test_update_draft_without_write_permission_is_denied(self):
		order = make_order_draft(user=DRAFT_USER)

		payload, _ = call_tool(
			RESTRICTED_USER,
			"update_draft",
			{"doctype": ORDER_DT, "name": order.name, "values": {"amount": 250}},
		)

		self.assertFalse(payload["ok"])
		self.assertIn(order.name, payload["error"])
		self.assertEqual(frappe.db.get_value(ORDER_DT, order.name, "amount"), 100)

	def test_a_suggest_agent_cannot_call_create_draft(self):
		"""The autonomy gate, not the permission gate: this user may create orders."""
		title = self.title()
		run = make_run(effective_user=DRAFT_USER, agent=AGENT)

		payload, _ = call_tool(
			DRAFT_USER, "create_draft", {"doctype": ORDER_DT, "values": self.values(title)}, run=run
		)

		self.assertFalse(payload["ok"])
		self.assertIn("Draft", payload["error"])
		self.assertIn("Suggest", payload["error"])
		self.assertFalse(frappe.db.exists(ORDER_DT, title))

		calls = tool_calls_for(run.name)
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].outcome, "Denied")
