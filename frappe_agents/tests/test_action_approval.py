# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Who may decide a proposal, and whose permissions apply it.

Separation of duties keeps three people away from the decision: whoever asked for
it, whoever the run acted as, and whoever owns the agent. An approval none of
them made is the only kind that means anything.

The apply itself runs as the approver, under the approver's own permissions and
with no `ignore_permissions` anywhere — so an approver who may not submit the
document cannot cause it to be submitted either. That is a failed action, not an
error: the row records it and the draft stays a draft.

Nothing here runs as Administrator. `frappe.get_roles("Administrator")` returns
every role on the site and `has_permission` short-circuits to True for it, so a
permission test run as Administrator asserts nothing at all.
"""

import frappe

from frappe_agents.actions import approve_action
from frappe_agents.api import list_pending_actions
from frappe_agents.tests.fixtures import (
	APPROVER_USER,
	BLIND_APPROVER,
	DRAFT_AGENT,
	DRAFT_USER,
	ORDER_DT,
	OWNED_AGENT,
	WEAK_APPROVER,
	AgentTestCase,
	as_user,
	make_order_draft,
	make_proposal,
	make_run,
	make_submitted_order,
)


class TestActionApproval(AgentTestCase):
	def modified_of(self, name: str, doctype: str = ORDER_DT):
		"""The timestamp an approver would be sending back from their screen."""
		return frappe.db.get_value(doctype, name, "modified")

	def action(self, name: str):
		return frappe.get_doc("Agent Action", name)

	def test_the_requester_cannot_approve_their_own_proposal(self):
		"""Even though this user holds Agent Approver and may submit the document."""
		order = make_order_draft(user=APPROVER_USER)
		action = make_proposal(order.name, user=APPROVER_USER)

		with as_user(APPROVER_USER):
			with self.assertRaises(frappe.PermissionError):
				approve_action(action, self.modified_of(order.name))

		self.assertEqual(self.action(action).status, "Pending")
		self.assertEqual(frappe.db.get_value(ORDER_DT, order.name, "docstatus"), 0)

	def test_the_user_the_run_acted_as_cannot_approve(self):
		"""The run's effective user is the one the agent borrowed, so they are out too."""
		order = make_order_draft(user=DRAFT_USER)
		run = make_run(effective_user=APPROVER_USER, agent=DRAFT_AGENT)
		action = make_proposal(order.name, user=DRAFT_USER, run=run)

		self.assertEqual(self.action(action).requested_by, DRAFT_USER)

		with as_user(APPROVER_USER):
			with self.assertRaises(frappe.PermissionError):
				approve_action(action, self.modified_of(order.name))

		self.assertEqual(self.action(action).status, "Pending")

	def test_the_agents_owner_cannot_approve_what_it_proposes(self):
		order = make_order_draft(user=DRAFT_USER)
		action = make_proposal(order.name, user=DRAFT_USER, agent=OWNED_AGENT)

		self.assertEqual(frappe.db.get_value("Agent", OWNED_AGENT, "owner"), APPROVER_USER)

		with as_user(APPROVER_USER):
			with self.assertRaises(frappe.PermissionError):
				approve_action(action, self.modified_of(order.name))

		self.assertEqual(self.action(action).status, "Pending")

	def test_a_user_without_the_approver_role_cannot_decide(self):
		order = make_order_draft(user=DRAFT_USER)
		action = make_proposal(order.name, user=DRAFT_USER)

		with as_user(DRAFT_USER):
			with self.assertRaises(frappe.PermissionError):
				approve_action(action, self.modified_of(order.name))

		self.assertEqual(self.action(action).status, "Pending")
		self.assertEqual(frappe.db.get_value(ORDER_DT, order.name, "docstatus"), 0)

	def test_a_third_party_approver_submits_the_document(self):
		order = make_order_draft(user=DRAFT_USER)
		action = make_proposal(order.name, user=DRAFT_USER)

		with as_user(APPROVER_USER):
			result = approve_action(action, self.modified_of(order.name), note="Checked the quotation.")

		self.assertEqual(result["status"], "Applied")
		self.assertEqual(result["docstatus"], 1)

		row = self.action(action)
		self.assertEqual(row.status, "Applied")
		self.assertEqual(row.decided_by, APPROVER_USER)
		self.assertTrue(row.decided_at)
		self.assertEqual(row.decision_note, "Checked the quotation.")
		self.assertEqual(row.edited_before_approval, 0)
		self.assertIn(order.name, row.applied_doc)
		self.assertFalse(row.failure)

		self.assertEqual(frappe.db.get_value(ORDER_DT, order.name, "docstatus"), 1)

	def test_an_approved_cancellation_cancels_the_document(self):
		order = make_submitted_order()
		action = make_proposal(order.name, action_type="Cancel", user=DRAFT_USER)

		with as_user(APPROVER_USER):
			result = approve_action(action, self.modified_of(order.name))

		self.assertEqual(result["status"], "Applied")
		self.assertEqual(self.action(action).status, "Applied")
		self.assertEqual(frappe.db.get_value(ORDER_DT, order.name, "docstatus"), 2)

	def test_an_approver_without_submit_permission_fails_the_action(self):
		"""The decision was theirs to make; the submit was not theirs to do."""
		order = make_order_draft(user=DRAFT_USER)
		action = make_proposal(order.name, user=DRAFT_USER)

		with as_user(WEAK_APPROVER):
			result = approve_action(action, self.modified_of(order.name))

		self.assertEqual(result["status"], "Failed")

		row = self.action(action)
		self.assertEqual(row.status, "Failed")
		self.assertEqual(row.decided_by, WEAK_APPROVER)
		self.assertTrue(row.decided_at)
		# The message comes off frappe.flags.error_message: a bare PermissionError
		# carries no string of its own, so an empty failure would mean the engine
		# recorded nothing an approver could act on.
		self.assertTrue(row.failure)

		self.assertEqual(frappe.db.get_value(ORDER_DT, order.name, "docstatus"), 0)

	def test_approver_without_target_read_cannot_apply_and_queue_redacts_target(self):
		"""Being allowed to submit the document is not being allowed to approve it.

		`BLIND_APPROVER` holds Agent Approver and submit on FA Test Order, and a
		User Permission pins them to the other project — so this order is one they
		cannot open. Two things follow. The apply fails closed *before* anything is
		written, which is a different outcome from the missing-submit case above:
		that one is recorded as Failed because the engine tried, this one never gets
		as far as trying. And the queue still lists the row, because it is their
		work list, with everything about the document taken out of it.
		"""
		order = make_order_draft(user=DRAFT_USER)
		action = make_proposal(order.name, user=DRAFT_USER)

		with as_user(BLIND_APPROVER):
			self.assertFalse(frappe.has_permission(ORDER_DT, "read", doc=order.name))
			with self.assertRaises(frappe.PermissionError):
				approve_action(action, self.modified_of(order.name))
			queue = list_pending_actions()

		self.assertEqual(self.action(action).status, "Pending")
		self.assertEqual(frappe.db.get_value(ORDER_DT, order.name, "docstatus"), 0)

		row = next(entry for entry in queue if entry["name"] == action)
		self.assertEqual(row["target_visible"], 0)
		self.assertIsNone(row["target_doctype"])
		self.assertIsNone(row["target_name"])
		self.assertIsNone(row["reason"])
		self.assertIsNone(row["target_modified"])
		self.assertEqual(row["can_decide"], 0)
		self.assertTrue(row["blocked_because"])
		self.assertNotIn(order.name, frappe.as_json(row))
		# What is about the proposal rather than about the document stays: they hold
		# read on the Agent Action, and a queue entry with nothing on it is noise.
		self.assertEqual(row["requested_by"], DRAFT_USER)
		self.assertEqual(row["action_type"], "Submit")

		# The approver who may read the order gets the whole row. That is what makes
		# the redaction above a permission answer and not a missing feature.
		with as_user(APPROVER_USER):
			visible = next(entry for entry in list_pending_actions() if entry["name"] == action)

		self.assertEqual(visible["target_visible"], 1)
		self.assertEqual(visible["target_doctype"], ORDER_DT)
		self.assertEqual(visible["target_name"], order.name)
		self.assertEqual(visible["can_decide"], 1)

	def test_a_target_that_disappeared_fails_the_action(self):
		order = make_order_draft(user=DRAFT_USER)
		action = make_proposal(order.name, user=DRAFT_USER)
		expected = self.modified_of(order.name)
		frappe.delete_doc(ORDER_DT, order.name, force=True, ignore_permissions=True)

		with as_user(APPROVER_USER):
			result = approve_action(action, expected)

		self.assertEqual(result["status"], "Failed")
		self.assertIn(order.name, self.action(action).failure)
