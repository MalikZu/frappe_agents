# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""One timestamp, two jobs.

`Agent Action.proposal_modified` is the target's `modified` at proposal time, and
the approver sends back the `modified` their screen was showing. The pair answers
two different questions:

* `expected_modified` against the document now — did anything change under the
  approver between looking and clicking? If so, refuse: the approval was for a
  version that no longer exists.
* `proposal_modified` against the document now — did a human change the draft
  between the proposal and the approval? If so, the review actually happened, and
  the row says so with `edited_before_approval`.

Frappe's own `TimestampMismatchError` cannot do the first job here: it compares
against the timestamp the in-memory document was loaded with, which on a
server-side approve is always current. The lock is entirely ours, so it is worth
a test that the right exception type comes out of it.
"""

import frappe
from frappe.utils import get_datetime

from frappe_agents.actions import approve_action
from frappe_agents.tests.fixtures import (
	APPROVER_USER,
	DRAFT_USER,
	ORDER_DT,
	SECOND_DRAFTER,
	AgentTestCase,
	as_user,
	call_tool,
	make_order_draft,
	make_proposal,
)


class TestApprovalLock(AgentTestCase):
	def modified_of(self, name: str):
		return frappe.db.get_value(ORDER_DT, name, "modified")

	def action(self, name: str):
		return frappe.get_doc("Agent Action", name)

	def edit_draft(self, name: str, amount: int = 777) -> None:
		"""A human changes the draft the agent proposed, through the same tools."""
		payload, _ = call_tool(
			SECOND_DRAFTER,
			"update_draft",
			{"doctype": ORDER_DT, "name": name, "values": {"amount": amount}},
		)
		self.assertTrue(payload["ok"], payload["error"])

	def test_approving_a_version_you_did_not_look_at_is_refused(self):
		order = make_order_draft(user=DRAFT_USER)
		action = make_proposal(order.name, user=DRAFT_USER)
		stale = self.modified_of(order.name)

		self.edit_draft(order.name)

		with as_user(APPROVER_USER):
			with self.assertRaises(frappe.TimestampMismatchError):
				approve_action(action, stale)

		self.assertEqual(self.action(action).status, "Pending")
		self.assertEqual(frappe.db.get_value(ORDER_DT, order.name, "docstatus"), 0)

	def test_an_unreadable_expected_modified_is_a_missing_lock(self):
		"""Fail closed: a timestamp that parses to nothing is not a match."""
		order = make_order_draft(user=DRAFT_USER)
		action = make_proposal(order.name, user=DRAFT_USER)

		with as_user(APPROVER_USER):
			with self.assertRaises(frappe.ValidationError):
				approve_action(action, "")

		self.assertEqual(self.action(action).status, "Pending")

	def test_an_untouched_draft_is_applied_without_the_edit_flag(self):
		order = make_order_draft(user=DRAFT_USER)
		action = make_proposal(order.name, user=DRAFT_USER)

		row = self.action(action)
		self.assertEqual(get_datetime(row.proposal_modified), get_datetime(self.modified_of(order.name)))

		with as_user(APPROVER_USER):
			result = approve_action(action, self.modified_of(order.name))

		self.assertEqual(result["status"], "Applied")
		self.assertEqual(result["edited_before_approval"], 0)
		self.assertEqual(self.action(action).edited_before_approval, 0)

	def test_an_edit_between_proposal_and_approval_is_recorded(self):
		"""The gate metric: somebody read the draft and changed it before saying yes."""
		order = make_order_draft(user=DRAFT_USER)
		action = make_proposal(order.name, user=DRAFT_USER)

		self.edit_draft(order.name)
		fresh = self.modified_of(order.name)
		self.assertNotEqual(get_datetime(fresh), get_datetime(self.action(action).proposal_modified))

		with as_user(APPROVER_USER):
			result = approve_action(action, fresh)

		self.assertEqual(result["status"], "Applied")
		self.assertEqual(result["edited_before_approval"], 1)

		row = self.action(action)
		self.assertEqual(row.edited_before_approval, 1)
		self.assertEqual(row.status, "Applied")
		self.assertEqual(frappe.db.get_value(ORDER_DT, order.name, "amount"), 777)
		self.assertEqual(frappe.db.get_value(ORDER_DT, order.name, "docstatus"), 1)

	def test_the_edit_flag_is_read_before_the_submit_overwrites_the_timestamp(self):
		"""submit() writes its own `modified`, so the comparison only exists before it."""
		order = make_order_draft(user=DRAFT_USER)
		action = make_proposal(order.name, user=DRAFT_USER)
		proposal_modified = self.action(action).proposal_modified

		with as_user(APPROVER_USER):
			approve_action(action, self.modified_of(order.name))

		submitted_modified = self.modified_of(order.name)
		self.assertNotEqual(get_datetime(submitted_modified), get_datetime(proposal_modified))
		self.assertEqual(self.action(action).edited_before_approval, 0)
