# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Proposing a write instead of making one.

`propose_submit` and `propose_cancel` write no document. They record one Agent
Action carrying the agent's reason and a snapshot of the target's `modified`,
and that snapshot is the whole approval mechanism: the lock the approver's
version is checked against, and the metric that says whether anyone read the
draft before saying yes.

The workflow refusal here is load-bearing, not defensive. Frappe does not block a
server-side `doc.submit()` on a workflow-governed doctype — it jumps the document
straight to the first submitted state, skipping every approval on the way — so
this refusal is the only thing standing between an agent proposal and that bypass.
"""

from unittest.mock import patch

import frappe
from frappe.model.workflow import get_workflow_name
from frappe.utils import get_datetime

from frappe_agents.tests.fixtures import (
	DRAFT_USER,
	ORDER_CANCELLED,
	ORDER_DT,
	ORDER_LIVE,
	AgentTestCase,
	actions_for,
	active_workflow,
	as_user,
	call_tool,
	make_order_draft,
	make_submitted_order,
	tool_calls_for,
)
from frappe_agents.tools.base import ToolDenied
from frappe_agents.tools.draft_tools import propose_submit

REASON = "The quantities match the signed quotation and the project is approved."


class TestProposals(AgentTestCase):
	def propose(self, tool: str, name: str, reason: str = REASON, user: str = DRAFT_USER, run=None):
		return call_tool(user, tool, {"doctype": ORDER_DT, "name": name, "reason": reason}, run=run)

	def test_propose_submit_records_a_pending_action_with_the_snapshot(self):
		order = make_order_draft(user=DRAFT_USER)

		payload, run = self.propose("propose_submit", order.name)

		self.assertTrue(payload["ok"], payload["error"])
		action = frappe.get_doc("Agent Action", payload["result"]["action"])
		self.assertEqual(action.status, "Pending")
		self.assertEqual(action.action_type, "Submit")
		self.assertEqual(action.target_doctype, ORDER_DT)
		self.assertEqual(action.target_name, order.name)
		self.assertEqual(action.requested_by, DRAFT_USER)
		self.assertEqual(action.run, run.name)
		self.assertEqual(action.agent, run.agent)
		self.assertEqual(action.reason, REASON)
		self.assertEqual(get_datetime(action.proposal_modified), get_datetime(order.modified))

		# The proposal is not the act: the document is untouched.
		self.assertEqual(frappe.db.get_value(ORDER_DT, order.name, "docstatus"), 0)

		calls = tool_calls_for(run.name)
		self.assertEqual(calls[0].outcome, "Success")
		self.assertIn(action.name, calls[0].docs_touched)

	def test_a_proposal_without_a_reason_is_refused(self):
		"""The reason is what the approver reads, so there is no proposing without one."""
		order = make_order_draft(user=DRAFT_USER)

		payload, _ = self.propose("propose_submit", order.name, reason="   ")

		self.assertFalse(payload["ok"])
		self.assertIn("reason", payload["error"])
		self.assertEqual(actions_for(order.name), [])

	def test_a_second_proposal_for_the_same_target_is_refused(self):
		order = make_order_draft(user=DRAFT_USER)

		first, run = self.propose("propose_submit", order.name)
		self.assertTrue(first["ok"], first["error"])

		second, _ = self.propose("propose_submit", order.name, run=run)

		self.assertFalse(second["ok"])
		self.assertIn(first["result"]["action"], second["error"])
		self.assertEqual(len(actions_for(order.name)), 1)

	def test_propose_submit_refuses_a_document_that_is_not_a_draft(self):
		payload, _ = self.propose("propose_submit", ORDER_LIVE)

		self.assertFalse(payload["ok"])
		self.assertIn("submitted", payload["error"].lower())
		self.assertEqual(actions_for(ORDER_LIVE), [])

	def test_propose_cancel_records_a_pending_action_for_a_submitted_document(self):
		order = make_submitted_order()

		payload, _ = self.propose("propose_cancel", order.name)

		self.assertTrue(payload["ok"], payload["error"])
		action = frappe.get_doc("Agent Action", payload["result"]["action"])
		self.assertEqual(action.action_type, "Cancel")
		self.assertEqual(action.status, "Pending")
		self.assertEqual(frappe.db.get_value(ORDER_DT, order.name, "docstatus"), 1)

	def test_propose_cancel_refuses_a_draft_and_a_cancelled_document(self):
		draft = make_order_draft(user=DRAFT_USER)

		on_draft, _ = self.propose("propose_cancel", draft.name)
		self.assertFalse(on_draft["ok"])
		self.assertIn("draft", on_draft["error"].lower())

		on_cancelled, _ = self.propose("propose_cancel", ORDER_CANCELLED)
		self.assertFalse(on_cancelled["ok"])
		self.assertIn("cancelled", on_cancelled["error"].lower())

		self.assertEqual(actions_for(draft.name), [])
		self.assertEqual(actions_for(ORDER_CANCELLED), [])

	def test_a_workflow_governed_doctype_is_refused(self):
		order = make_order_draft(user=DRAFT_USER)

		with active_workflow(ORDER_DT):
			self.assertTrue(get_workflow_name(ORDER_DT))
			payload, _ = self.propose("propose_submit", order.name)

		self.assertFalse(payload["ok"])
		self.assertIn("Workflow", payload["error"])
		self.assertEqual(actions_for(order.name), [])

	def test_proposals_work_again_once_the_workflow_is_gone(self):
		"""The refusal follows the Workflow, not a cached answer about it."""
		order = make_order_draft(user=DRAFT_USER)

		with active_workflow(ORDER_DT):
			refused, run = self.propose("propose_submit", order.name)
		self.assertFalse(refused["ok"])

		payload, _ = self.propose("propose_submit", order.name, run=run)

		self.assertTrue(payload["ok"], payload["error"])
		self.assertEqual(len(actions_for(order.name)), 1)

	def test_a_proposal_outside_a_run_is_refused(self):
		"""A proposal records who asked, and outside a run there is nobody to record."""
		order = make_order_draft(user=DRAFT_USER)

		with as_user(DRAFT_USER):
			with self.assertRaises(ToolDenied):
				propose_submit({"doctype": ORDER_DT, "name": order.name, "reason": REASON})

		self.assertEqual(actions_for(order.name), [])

	def test_a_proposal_publishes_the_action_to_the_run(self):
		order = make_order_draft(user=DRAFT_USER)

		with patch("frappe_agents.tools.draft_tools.publish_event") as publish:
			payload, _ = self.propose("propose_submit", order.name)

		self.assertTrue(payload["ok"], payload["error"])
		publish.assert_called_once()
		args, kwargs = publish.call_args
		self.assertEqual(args[1], "action_proposed")
		self.assertEqual(kwargs["action"], payload["result"]["action"])
		self.assertEqual(kwargs["target_name"], order.name)
