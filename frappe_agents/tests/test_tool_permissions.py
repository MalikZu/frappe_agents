# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The permission gate: row level, field level, and doctype level.

Every assertion here is about a real Frappe permission — a User Permission, a
permlevel, a missing read rule — reaching the model through the tool layer.
"""

import frappe

from frappe_agents.tests.fixtures import (
	ALPHA_SECRET,
	OPEN_USER,
	RESTRICTED_USER,
	TICKET_ALPHA,
	TICKET_BETA,
	TICKET_DT,
	VAULT_DT,
	AgentTestCase,
	as_user,
	make_run,
	tool_calls_for,
)
from frappe_agents.tools.base import ToolDenied, execute_tool
from frappe_agents.tools.read_tools import RESTRICTED, search_documents


class TestToolPermissions(AgentTestCase):
	def search_as(self, user: str, args: dict) -> tuple[dict, str]:
		"""Call search_documents through the tool layer as `user`."""
		run = make_run(effective_user=user)
		with as_user(user):
			payload = execute_tool(run, "search_documents", args)
		return payload, run.name

	def test_user_permission_hides_rows(self):
		"""A User Permission on the linked project decides which rows exist at all."""
		args = {"doctype": TICKET_DT, "fields": ["name", "subject", "project"], "limit": 50}

		payload, _ = self.search_as(RESTRICTED_USER, args)
		self.assertTrue(payload["ok"], payload["error"])
		self.assertEqual({row["name"] for row in payload["result"]["rows"]}, {TICKET_ALPHA})

		payload, _ = self.search_as(OPEN_USER, args)
		self.assertTrue(payload["ok"], payload["error"])
		open_names = {row["name"] for row in payload["result"]["rows"]}
		self.assertIn(TICKET_ALPHA, open_names)
		self.assertIn(TICKET_BETA, open_names)

	def test_user_permission_survives_an_explicit_filter(self):
		"""Asking for the hidden row by name still does not return it."""
		payload, _ = self.search_as(
			RESTRICTED_USER,
			{"doctype": TICKET_DT, "filters": {"name": TICKET_BETA}, "fields": ["name"]},
		)
		self.assertTrue(payload["ok"], payload["error"])
		self.assertEqual(payload["result"]["rows"], [])

	def test_permlevel_field_comes_back_marked(self):
		"""A field above the user's permlevel is marked, never silently dropped."""
		args = {"doctype": TICKET_DT, "fields": ["name", "subject", "secret_note"], "limit": 50}

		payload, _ = self.search_as(RESTRICTED_USER, args)
		self.assertTrue(payload["ok"], payload["error"])
		result = payload["result"]
		self.assertIn("secret_note", result["restricted_fields"])
		self.assertEqual(self._row(result, TICKET_ALPHA)["secret_note"], RESTRICTED)
		self.assertNotIn(ALPHA_SECRET, frappe.as_json(result))

		payload, _ = self.search_as(OPEN_USER, args)
		self.assertTrue(payload["ok"], payload["error"])
		result = payload["result"]
		self.assertEqual(result["restricted_fields"], [])
		self.assertEqual(self._row(result, TICKET_ALPHA)["secret_note"], ALPHA_SECRET)

	def test_permlevel_field_cannot_be_used_as_a_filter(self):
		"""Filtering on a field you may not read would leak it one bit at a time."""
		payload, run_name = self.search_as(
			RESTRICTED_USER,
			{"doctype": TICKET_DT, "filters": {"secret_note": ALPHA_SECRET}, "fields": ["name"]},
		)
		self.assertFalse(payload["ok"])
		self.assertIn("secret_note", payload["error"])
		self.assertEqual([call.outcome for call in tool_calls_for(run_name)], ["Denied"])

	def test_permlevel_meta_lists_restricted_fields(self):
		run = make_run(effective_user=RESTRICTED_USER)
		with as_user(RESTRICTED_USER):
			payload = execute_tool(run, "get_doctype_meta", {"doctype": TICKET_DT})

		self.assertTrue(payload["ok"], payload["error"])
		fieldnames = {field["fieldname"] for field in payload["result"]["fields"]}
		self.assertIn("subject", fieldnames)
		self.assertNotIn("secret_note", fieldnames)
		self.assertIn("secret_note", payload["result"]["restricted_fields"])

	def test_doctype_without_read_permission_is_denied_and_logged(self):
		"""No read rule for the user's roles: denied, and the denial is on the record."""
		run = make_run(effective_user=RESTRICTED_USER)

		with as_user(RESTRICTED_USER):
			self.assertRaises(ToolDenied, search_documents, {"doctype": VAULT_DT})
			payload = execute_tool(run, "search_documents", {"doctype": VAULT_DT})

		self.assertFalse(payload["ok"])
		self.assertIsNone(payload["result"])
		self.assertIn(VAULT_DT, payload["error"])

		calls = tool_calls_for(run.name)
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].outcome, "Denied")
		self.assertEqual(calls[0].tool, "search_documents")

	def _row(self, result: dict, name: str) -> dict:
		for row in result["rows"]:
			if row["name"] == name:
				return row
		self.fail(f"{name} not in result: {result['rows']}")
