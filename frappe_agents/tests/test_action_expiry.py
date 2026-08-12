# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Proposals nobody decided.

A pending proposal is a claim about a document as it was; after enough days it is
a claim about a document nobody has looked at in a week. The daily sweep expires
those, and the agent proposes again if it still wants to.

The scheduler is switched off for the whole test run, so `expire_stale_actions`
is called by hand here — which is the honest test anyway: it exercises the
function rather than RQ.
"""

import frappe
from frappe.utils import add_to_date, now_datetime

from frappe_agents.actions import approve_action, expire_stale_actions, is_stale, reject_action
from frappe_agents.tests.fixtures import (
	APPROVER_USER,
	DRAFT_USER,
	ORDER_DT,
	AgentTestCase,
	as_user,
	make_order_draft,
	make_proposal,
)


class TestActionExpiry(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.set_expiry_days(7)
		# The sweep is site-wide, and the counts below are about one row. Drain
		# anything an earlier session left pending before proposing this one.
		expire_stale_actions()
		self.order = make_order_draft(user=DRAFT_USER)
		self.action_name = make_proposal(self.order.name, user=DRAFT_USER)

	def set_expiry_days(self, days: int) -> None:
		frappe.db.set_single_value("Agent Settings", "action_expiry_days", days)
		frappe.clear_document_cache("Agent Settings", "Agent Settings")

	def action(self):
		return frappe.get_doc("Agent Action", self.action_name)

	def backdate(self, days: int) -> None:
		"""Age the proposal by moving its creation, the way the clock would."""
		frappe.db.set_value(
			"Agent Action",
			self.action_name,
			"creation",
			add_to_date(now_datetime(), days=-days),
			update_modified=False,
		)

	def test_a_fresh_proposal_is_not_stale_and_the_sweep_leaves_it_alone(self):
		self.assertFalse(is_stale(self.action()))

		expire_stale_actions()

		self.assertEqual(self.action().status, "Pending")

	def test_a_backdated_proposal_is_stale_and_the_sweep_expires_it(self):
		self.backdate(30)
		self.assertTrue(is_stale(self.action()))

		expired = expire_stale_actions()

		self.assertEqual(expired, 1)
		row = self.action()
		self.assertEqual(row.status, "Expired")
		self.assertTrue(row.failure)
		# Nobody decided it, so nobody is recorded as having decided it.
		self.assertFalse(row.decided_by)

	def test_the_sweep_is_idempotent(self):
		self.backdate(30)
		self.assertEqual(expire_stale_actions(), 1)
		before = self.action().modified

		self.assertEqual(expire_stale_actions(), 0)

		row = self.action()
		self.assertEqual(row.status, "Expired")
		# Expiry is the clock, not an edit: the second pass matches nothing and the
		# first one left `modified` where it was.
		self.assertEqual(row.modified, before)

	def test_the_expiry_window_comes_from_agent_settings(self):
		self.set_expiry_days(1)
		self.backdate(2)

		self.assertTrue(is_stale(self.action()))
		self.assertEqual(expire_stale_actions(), 1)
		self.assertEqual(self.action().status, "Expired")

	def test_a_window_that_has_not_passed_yet_keeps_the_proposal_pending(self):
		self.set_expiry_days(30)
		self.backdate(2)

		self.assertFalse(is_stale(self.action()))
		self.assertEqual(expire_stale_actions(), 0)
		self.assertEqual(self.action().status, "Pending")

	def test_a_stale_proposal_is_refused_before_the_sweep_reaches_it(self):
		"""The window is what makes it dead, not the row having been swept."""
		self.backdate(30)

		with as_user(APPROVER_USER):
			with self.assertRaises(frappe.ValidationError):
				approve_action(self.action_name, frappe.db.get_value(ORDER_DT, self.order.name, "modified"))

		self.assertEqual(self.action().status, "Pending")
		self.assertEqual(frappe.db.get_value(ORDER_DT, self.order.name, "docstatus"), 0)

	def test_an_expired_proposal_cannot_be_approved_or_rejected(self):
		self.backdate(30)
		expire_stale_actions()

		with as_user(APPROVER_USER):
			with self.assertRaises(frappe.ValidationError):
				approve_action(self.action_name, frappe.db.get_value(ORDER_DT, self.order.name, "modified"))

			with self.assertRaises(frappe.ValidationError):
				reject_action(self.action_name, note="Too late anyway.")

		self.assertEqual(self.action().status, "Expired")
		self.assertEqual(frappe.db.get_value(ORDER_DT, self.order.name, "docstatus"), 0)
