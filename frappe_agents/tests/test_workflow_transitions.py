# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Proposing a Workflow action, and applying it as the approver.

A document a Workflow governs never gets a submit or cancel proposal: Frappe
would let that jump the workflow. It gets `propose_transition` instead, which
names one of the workflow's own actions. The tool checks the action is open to
the user the run acts as. The approver applies it through Frappe's
`apply_workflow`, in their own session, so the Workflow checks the approver's
roles, the transition's condition and self-approval itself.

The fixture Workflow has two states and one action, and the approved state is
submitted. Which roles the action is open to changes from test to test:

* `DRAFTER_ROLE` is held by `DRAFT_USER`, who proposes.
* `SUBMITTER_ROLE` is held by `APPROVER_USER`, who approves.

Nothing here runs as Administrator: `apply_workflow` lets Administrator
self-approve and `get_roles` hands it every role, so a test run as
Administrator proves nothing.
"""

from pathlib import Path

import frappe
from frappe.model.workflow import apply_workflow

from frappe_agents.actions import approve_action
from frappe_agents.tests.fixtures import (
	APPROVER_USER,
	DRAFT_USER,
	DRAFTER_ROLE,
	ORDER_DT,
	SUBMITTER_ROLE,
	WORKFLOW_ACTION,
	WORKFLOW_APPROVED_STATE,
	WORKFLOW_DRAFT_STATE,
	AgentTestCase,
	actions_for,
	active_workflow,
	as_user,
	call_tool,
	make_order_draft,
)

REASON = "The amount matches the signed quotation, and the project is approved."
BOTH = (DRAFTER_ROLE, SUBMITTER_ROLE)

APP_ROOT = Path(__file__).resolve().parents[1]
# Every name that moves a workflow state. apply_workflow is the public one;
# run_workflow_transition is what it calls, and is just as able to submit.
WORKFLOW_APPLIERS = ("apply_workflow", "run_workflow_transition")


class TestWorkflowTransitions(AgentTestCase):
	def propose(self, name: str, action: str = WORKFLOW_ACTION, user: str = DRAFT_USER, run=None):
		return call_tool(
			user,
			"propose_transition",
			{"doctype": ORDER_DT, "name": name, "action": action, "reason": REASON},
			run=run,
		)

	def proposed(self, name: str, user: str = DRAFT_USER) -> str:
		payload, _ = self.propose(name, user=user)
		self.assertTrue(payload["ok"], payload["error"])
		return payload["result"]["action"]

	def modified_of(self, name: str):
		return frappe.db.get_value(ORDER_DT, name, "modified")

	def state_of(self, name: str) -> tuple:
		return tuple(frappe.db.get_value(ORDER_DT, name, ["docstatus", "workflow_state"]))

	# -- proposing -----------------------------------------------------------

	def test_an_open_action_records_a_pending_transition(self):
		with active_workflow(ORDER_DT, roles=BOTH):
			order = make_order_draft(user=DRAFT_USER)
			payload, run = self.propose(order.name)

			self.assertTrue(payload["ok"], payload["error"])
			action = frappe.get_doc("Agent Action", payload["result"]["action"])
			self.assertEqual(action.status, "Pending")
			self.assertEqual(action.action_type, "Workflow Transition")
			self.assertEqual(action.workflow_action, WORKFLOW_ACTION)
			self.assertEqual(action.requested_by, DRAFT_USER)
			self.assertEqual(action.run, run.name)
			self.assertEqual(payload["result"]["workflow_state"], WORKFLOW_DRAFT_STATE)

			# The proposal is not the act.
			self.assertEqual(self.state_of(order.name), (0, WORKFLOW_DRAFT_STATE))

	def test_an_action_not_open_to_the_running_user_is_refused_with_the_open_ones(self):
		with active_workflow(ORDER_DT, roles=(DRAFTER_ROLE,)):
			order = make_order_draft(user=DRAFT_USER)
			payload, _ = self.propose(order.name, action="FA Workflow Reopen")

		self.assertFalse(payload["ok"])
		self.assertIn("FA Workflow Reopen", payload["error"])
		# The refusal says what the agent may propose instead.
		self.assertIn(WORKFLOW_ACTION, payload["error"])
		self.assertEqual(actions_for(order.name), [])

	def test_an_action_the_running_user_holds_no_role_for_is_refused(self):
		"""The agent proposes nothing its user could not have started."""
		with active_workflow(ORDER_DT, roles=(SUBMITTER_ROLE,)):
			order = make_order_draft(user=DRAFT_USER)
			payload, _ = self.propose(order.name)

		self.assertFalse(payload["ok"])
		self.assertIn("none", payload["error"])
		self.assertEqual(actions_for(order.name), [])

	def test_a_doctype_without_a_workflow_is_pointed_to_propose_submit(self):
		order = make_order_draft(user=DRAFT_USER)

		payload, _ = self.propose(order.name)

		self.assertFalse(payload["ok"])
		self.assertIn("propose_submit", payload["error"])
		self.assertEqual(actions_for(order.name), [])

	def test_propose_submit_on_a_workflow_doctype_is_still_refused(self):
		with active_workflow(ORDER_DT, roles=BOTH):
			order = make_order_draft(user=DRAFT_USER)
			payload, _ = call_tool(
				DRAFT_USER, "propose_submit", {"doctype": ORDER_DT, "name": order.name, "reason": REASON}
			)

		self.assertFalse(payload["ok"])
		self.assertIn("Workflow", payload["error"])
		self.assertIn("propose_transition", payload["error"])
		self.assertEqual(actions_for(order.name), [])

	def test_a_second_proposal_of_the_same_action_is_refused(self):
		with active_workflow(ORDER_DT, roles=BOTH):
			order = make_order_draft(user=DRAFT_USER)
			first, run = self.propose(order.name)
			second, _ = self.propose(order.name, run=run)

		self.assertTrue(first["ok"], first["error"])
		self.assertFalse(second["ok"])
		self.assertIn(first["result"]["action"], second["error"])
		self.assertEqual(len(actions_for(order.name)), 1)

	# -- approving -----------------------------------------------------------

	def test_the_approver_applies_the_transition_under_their_own_roles(self):
		with active_workflow(ORDER_DT, roles=BOTH):
			order = make_order_draft(user=DRAFT_USER)
			action = self.proposed(order.name)

			with as_user(APPROVER_USER):
				result = approve_action(action, self.modified_of(order.name), note="Checked the quote.")

			self.assertEqual(result["status"], "Applied")
			self.assertEqual(result["docstatus"], 1)
			row = frappe.get_doc("Agent Action", action)
			self.assertEqual(row.status, "Applied")
			self.assertEqual(row.decided_by, APPROVER_USER)
			self.assertEqual(row.edited_before_approval, 0)
			self.assertEqual(self.state_of(order.name), (1, WORKFLOW_APPROVED_STATE))

	def test_an_approver_without_the_transition_role_fails_the_action(self):
		"""May submit the document, may not take this workflow step: the Workflow says no."""
		with active_workflow(ORDER_DT, roles=(DRAFTER_ROLE,)):
			order = make_order_draft(user=DRAFT_USER)
			action = self.proposed(order.name)

			with as_user(APPROVER_USER):
				result = approve_action(action, self.modified_of(order.name))

			self.assertEqual(result["status"], "Failed")
			row = frappe.get_doc("Agent Action", action)
			self.assertEqual(row.status, "Failed")
			self.assertEqual(row.decided_by, APPROVER_USER)
			self.assertTrue(row.failure)
			self.assertEqual(self.state_of(order.name), (0, WORKFLOW_DRAFT_STATE))

	def test_self_approval_is_refused_where_the_transition_says_so(self):
		"""The approver drafted the document, and the transition forbids approving your own."""
		with active_workflow(ORDER_DT, roles=BOTH, allow_self_approval=0):
			order = make_order_draft(user=APPROVER_USER)
			action = self.proposed(order.name)

			with as_user(APPROVER_USER):
				result = approve_action(action, self.modified_of(order.name))

			self.assertEqual(result["status"], "Failed")
			self.assertIn("Self approval", frappe.db.get_value("Agent Action", action, "failure"))
			self.assertEqual(self.state_of(order.name), (0, WORKFLOW_DRAFT_STATE))

	def test_a_state_change_after_the_proposal_is_refused_then_failed(self):
		with active_workflow(ORDER_DT, roles=BOTH):
			order = make_order_draft(user=DRAFT_USER)
			action = self.proposed(order.name)
			stale = self.modified_of(order.name)

			# Someone takes the step by hand in the desk before the queue gets to it.
			with as_user(APPROVER_USER):
				apply_workflow(frappe.get_doc(ORDER_DT, order.name), WORKFLOW_ACTION)

			# The approver decided from the old screen: the lock refuses.
			with as_user(APPROVER_USER):
				with self.assertRaises(frappe.TimestampMismatchError):
					approve_action(action, stale)
			self.assertEqual(frappe.db.get_value("Agent Action", action, "status"), "Pending")

			# Reloaded and approved again: the Workflow has no such step from here.
			with as_user(APPROVER_USER):
				result = approve_action(action, self.modified_of(order.name))
			self.assertEqual(result["status"], "Failed")
			self.assertEqual(frappe.db.get_value("Agent Action", action, "edited_before_approval"), 1)

	def test_the_requester_cannot_approve_their_own_transition(self):
		with active_workflow(ORDER_DT, roles=BOTH):
			order = make_order_draft(user=APPROVER_USER)
			action = self.proposed(order.name, user=APPROVER_USER)

			with as_user(APPROVER_USER):
				with self.assertRaises(frappe.PermissionError):
					approve_action(action, self.modified_of(order.name))

			self.assertEqual(frappe.db.get_value("Agent Action", action, "status"), "Pending")
			self.assertEqual(self.state_of(order.name), (0, WORKFLOW_DRAFT_STATE))

	def test_a_workflow_switched_off_before_approval_fails_the_action(self):
		with active_workflow(ORDER_DT, roles=BOTH):
			order = make_order_draft(user=DRAFT_USER)
			action = self.proposed(order.name)

		with as_user(APPROVER_USER):
			result = approve_action(action, self.modified_of(order.name))

		self.assertEqual(result["status"], "Failed")
		self.assertIn("Workflow", frappe.db.get_value("Agent Action", action, "failure"))
		self.assertEqual(frappe.db.get_value(ORDER_DT, order.name, "docstatus"), 0)

	# -- the wall ------------------------------------------------------------

	def test_no_tool_module_can_apply_a_workflow(self):
		"""apply_workflow can submit and cancel. No tool may reach it.

		A transition is applied only by approve_action, as the approver. A tool
		that imported it would hand an agent a way past "agents draft, humans submit".
		"""
		offenders = [
			str(path.relative_to(APP_ROOT))
			for path in sorted((APP_ROOT / "tools").rglob("*.py"))
			if any(name in path.read_text() for name in WORKFLOW_APPLIERS)
		]
		self.assertEqual(offenders, [])

	def test_only_the_approval_engine_applies_a_workflow(self):
		callers = [
			str(path.relative_to(APP_ROOT))
			for path in sorted(APP_ROOT.rglob("*.py"))
			if "tests" not in path.parts and any(name in path.read_text() for name in WORKFLOW_APPLIERS)
		]
		self.assertEqual(callers, ["actions.py"])
