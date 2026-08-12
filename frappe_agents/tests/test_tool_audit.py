# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The audit trail.

One tool call, one Agent Tool Call row — whether it succeeded, was denied, or
blew up. A run with no rows means nothing happened; that is the property an
auditor relies on.
"""

import frappe

from frappe_agents.tests.fixtures import (
	RESTRICTED_USER,
	TICKET_ALPHA,
	TICKET_DT,
	VAULT_DT,
	AgentTestCase,
	as_user,
	make_run,
	tool_calls_for,
)
from frappe_agents.tools.base import execute_tool


class TestToolAudit(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.run = make_run(effective_user=RESTRICTED_USER)

	def test_every_outcome_logs_exactly_one_row(self):
		with as_user(RESTRICTED_USER):
			success = execute_tool(self.run, "search_documents", {"doctype": TICKET_DT, "fields": ["name"]})
			denied = execute_tool(self.run, "search_documents", {"doctype": VAULT_DT})
			errored = execute_tool(
				self.run, "search_documents", {"doctype": TICKET_DT, "fields": ["no_such_field"]}
			)

		self.assertTrue(success["ok"], success["error"])
		self.assertFalse(denied["ok"])
		self.assertFalse(errored["ok"])

		calls = tool_calls_for(self.run.name)
		self.assertEqual(len(calls), 3)
		self.assertEqual(sorted(call.outcome for call in calls), ["Denied", "Error", "Success"])
		for call in calls:
			self.assertEqual(call.tool, "search_documents")
			self.assertTrue(call.args_json)
			self.assertGreaterEqual(call.duration_ms, 0)

	def test_success_row_records_what_was_read(self):
		with as_user(RESTRICTED_USER):
			execute_tool(self.run, "search_documents", {"doctype": TICKET_DT, "fields": ["name", "subject"]})

		calls = tool_calls_for(self.run.name)
		self.assertEqual(len(calls), 1)
		call = calls[0]
		self.assertEqual(call.outcome, "Success")
		self.assertIn(TICKET_DT, call.args_json)
		self.assertIn(TICKET_ALPHA, call.docs_touched)
		self.assertTrue(call.result_summary)
		self.assertFalse(call.error)

	def test_row_belongs_to_the_run_and_is_readable_by_its_owner(self):
		with as_user(RESTRICTED_USER):
			execute_tool(self.run, "search_documents", {"doctype": TICKET_DT, "fields": ["name"]})
			own_rows = frappe.get_list(
				"Agent Tool Call",
				filters={"run": self.run.name},
				fields=["name", "outcome"],
				limit_page_length=0,
			)

		self.assertEqual(len(own_rows), 1)
		self.assertEqual(frappe.db.get_value("Agent Tool Call", own_rows[0].name, "owner"), RESTRICTED_USER)

	def test_tool_the_agent_was_not_given_is_denied_and_logged(self):
		with as_user(RESTRICTED_USER):
			payload = execute_tool(self.run, "not_a_tool", {"doctype": TICKET_DT})

		self.assertFalse(payload["ok"])
		calls = tool_calls_for(self.run.name)
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].outcome, "Denied")
		self.assertEqual(calls[0].tool, "not_a_tool")

	def test_no_role_may_edit_or_delete_an_audit_row(self):
		meta = frappe.get_meta("Agent Tool Call")
		for perm in meta.permissions:
			self.assertFalse(perm.write, f"{perm.role} can write Agent Tool Call")
			self.assertFalse(perm.delete, f"{perm.role} can delete Agent Tool Call")
			self.assertFalse(perm.create, f"{perm.role} can create Agent Tool Call")
