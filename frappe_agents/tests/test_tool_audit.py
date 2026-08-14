# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The audit trail.

One tool call, one Agent Tool Call row — whether it succeeded, was denied, or
blew up. A run with no rows means nothing happened; that is the property an
auditor relies on.

The row is also durable and widely read, so the second class here holds the
other half of that bargain: the row says what happened and never repeats the
values that must not be kept.
"""

import frappe

from frappe_agents.tests.fixtures import (
	ALTERED_IBAN,
	DRAFT_AGENT,
	DRAFT_USER,
	IBAN_FIELD,
	ORDER_DT,
	PASSWORD_FIELD,
	PROJECT_ALPHA,
	RESTRICTED_USER,
	TICKET_ALPHA,
	TICKET_DT,
	VAULT_DT,
	VENDOR_DT,
	VENDOR_IBAN,
	AgentTestCase,
	as_user,
	call_tool,
	make_run,
	tool_calls_for,
)
from frappe_agents.tools.base import REDACTED, execute_tool


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


class TestToolAuditRedaction(AgentTestCase):
	"""What the row may not keep.

	An Agent Tool Call is durable and Agent Auditor reads it, so a password, a
	token or a bank account that travelled through a tool argument must not be
	stored there — while the tool, the doctype, the field names and the outcome
	stay fully legible, because a row that hides those audits nothing.
	"""

	PASSWORD = "portal-pass-never-stored"
	TOKEN = "sk-live-never-stored-either"

	def test_tool_audit_redacts_sensitive_arguments_on_success_and_error(self):
		title = f"FA Redact {frappe.generate_hash(length=8)}"
		run = make_run(effective_user=DRAFT_USER, agent=DRAFT_AGENT)

		created, _ = call_tool(
			DRAFT_USER,
			"create_draft",
			{
				"doctype": ORDER_DT,
				"values": {
					"order_title": title,
					"project": PROJECT_ALPHA,
					"amount": 50,
					# A Password field, an administrator-marked field, and the same
					# marked field one level down inside a child row.
					PASSWORD_FIELD: self.PASSWORD,
					IBAN_FIELD: VENDOR_IBAN,
					"items": [{"item": "FA Widget", "qty": 1, IBAN_FIELD: ALTERED_IBAN}],
				},
			},
			run=run,
		)
		self.assertTrue(created["ok"], created["error"])
		order = created["result"]["name"]

		updated, _ = call_tool(
			DRAFT_USER,
			"update_draft",
			{
				"doctype": ORDER_DT,
				"name": order,
				"values": {PASSWORD_FIELD: self.PASSWORD, IBAN_FIELD: ALTERED_IBAN},
			},
			run=run,
		)
		self.assertTrue(updated["ok"], updated["error"])

		# A filter on a masked field: the value is in the read, not in a write.
		call_tool(
			DRAFT_USER,
			"search_documents",
			{"doctype": VENDOR_DT, "fields": ["name"], "filters": {IBAN_FIELD: VENDOR_IBAN}},
			run=run,
		)

		# And the failing call. `token` is no field of this doctype, so the write is
		# refused — with the secret it carried already out of the row.
		failed, _ = call_tool(
			DRAFT_USER,
			"update_draft",
			{
				"doctype": ORDER_DT,
				"name": order,
				"values": {PASSWORD_FIELD: self.PASSWORD, "token": self.TOKEN},
			},
			run=run,
		)
		self.assertFalse(failed["ok"])

		calls = tool_calls_for(run.name)
		self.assertEqual(len(calls), 4)
		self.assertEqual(
			[call.tool for call in calls],
			["create_draft", "update_draft", "search_documents", "update_draft"],
		)
		self.assertEqual([call.outcome for call in calls[:2]], ["Success", "Success"])
		self.assertEqual(calls[3].outcome, "Error")

		secrets = (self.PASSWORD, self.TOKEN, VENDOR_IBAN, ALTERED_IBAN)
		for call in calls:
			stored = " ".join(part for part in (call.args_json, call.result_summary, call.error) if part)
			for secret in secrets:
				self.assertNotIn(secret, stored, f"{call.tool} row kept {secret}")

		create_args, update_args, search_args, failed_args = (call.args_json for call in calls)

		# What was done stays readable: the doctype, every field name, and the
		# marker that says a value was held back.
		for args_json in (create_args, update_args, failed_args):
			self.assertIn(ORDER_DT, args_json)
			self.assertIn(PASSWORD_FIELD, args_json)
			self.assertIn(REDACTED, args_json)

		self.assertIn(IBAN_FIELD, create_args)
		self.assertIn("order_title", create_args)
		self.assertIn(title, create_args)
		self.assertIn("items", create_args)
		self.assertIn("FA Widget", create_args)

		self.assertIn(IBAN_FIELD, update_args)
		self.assertIn(order, update_args)

		self.assertIn(VENDOR_DT, search_args)
		self.assertIn(IBAN_FIELD, search_args)
		self.assertIn(REDACTED, search_args)

		self.assertIn("token", failed_args)
		self.assertIn("token", calls[3].error)
