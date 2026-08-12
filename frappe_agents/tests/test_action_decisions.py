# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The other three ways a proposal ends: refused by the kill switch, rejected
with a note, or already decided.

The kill switch is asymmetric on purpose. `global_enabled = 0` means the site
distrusts the runtime, and a human applying agent output during that state is
still acting on agent output — so approving is blocked. Rejecting is not:
clearing the queue is exactly what a site with the runtime switched off should
still be able to do.
"""

import frappe

from frappe_agents.actions import approve_action, reject_action
from frappe_agents.tests.fixtures import (
	APPROVER_USER,
	DRAFT_USER,
	ORDER_DT,
	AgentTestCase,
	as_user,
	make_order_draft,
	make_proposal,
	set_kill_switch,
)


class TestActionDecisions(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.order = make_order_draft(user=DRAFT_USER)
		self.action_name = make_proposal(self.order.name, user=DRAFT_USER)

	def modified(self):
		return frappe.db.get_value(ORDER_DT, self.order.name, "modified")

	def action(self):
		return frappe.get_doc("Agent Action", self.action_name)

	def disable_runtime(self) -> None:
		set_kill_switch(0)
		self.addCleanup(set_kill_switch, 1)

	def test_the_kill_switch_blocks_an_approval(self):
		self.disable_runtime()

		with as_user(APPROVER_USER):
			with self.assertRaises(frappe.ValidationError):
				approve_action(self.action_name, self.modified())

		self.assertEqual(self.action().status, "Pending")
		self.assertEqual(frappe.db.get_value(ORDER_DT, self.order.name, "docstatus"), 0)

	def test_the_kill_switch_does_not_block_a_rejection(self):
		"""A site with the runtime off must still be able to empty its queue."""
		self.disable_runtime()

		with as_user(APPROVER_USER):
			result = reject_action(self.action_name, note="Not while the runtime is off.")

		self.assertEqual(result["status"], "Rejected")

		row = self.action()
		self.assertEqual(row.status, "Rejected")
		self.assertEqual(row.decided_by, APPROVER_USER)
		self.assertTrue(row.decided_at)
		self.assertEqual(row.decision_note, "Not while the runtime is off.")
		self.assertEqual(frappe.db.get_value(ORDER_DT, self.order.name, "docstatus"), 0)

	def test_a_rejection_without_a_note_is_refused(self):
		"""The note is the answer to the proposal, so there is no rejecting without one."""
		with as_user(APPROVER_USER):
			with self.assertRaises(frappe.ValidationError):
				reject_action(self.action_name, note="   ")

		self.assertEqual(self.action().status, "Pending")

	def test_a_rejection_leaves_the_edit_flag_alone(self):
		"""It measures what happened to a document that was applied. None was."""
		with as_user(APPROVER_USER):
			reject_action(self.action_name, note="The quotation is not signed yet.")

		row = self.action()
		self.assertEqual(row.edited_before_approval, 0)
		self.assertFalse(row.applied_doc)

	def test_a_requester_cannot_reject_their_own_proposal(self):
		order = make_order_draft(user=APPROVER_USER)
		action = make_proposal(order.name, user=APPROVER_USER)

		with as_user(APPROVER_USER):
			with self.assertRaises(frappe.PermissionError):
				reject_action(action, note="Withdrawing my own request.")

		self.assertEqual(frappe.get_doc("Agent Action", action).status, "Pending")

	def test_a_decided_proposal_cannot_be_decided_again(self):
		with as_user(APPROVER_USER):
			reject_action(self.action_name, note="Superseded by a manual order.")

			with self.assertRaises(frappe.ValidationError):
				approve_action(self.action_name, self.modified())

		row = self.action()
		self.assertEqual(row.status, "Rejected")
		self.assertEqual(frappe.db.get_value(ORDER_DT, self.order.name, "docstatus"), 0)

	def test_an_applied_proposal_cannot_be_approved_twice(self):
		with as_user(APPROVER_USER):
			approve_action(self.action_name, self.modified())

			with self.assertRaises(frappe.ValidationError):
				approve_action(self.action_name, self.modified())

		self.assertEqual(self.action().status, "Applied")
