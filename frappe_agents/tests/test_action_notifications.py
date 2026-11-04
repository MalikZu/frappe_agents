# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Who is told about a proposal, and when.

A proposal nobody hears about waits until it expires. So the approvers who could
decide it get a Notification Log when it is recorded, and the people who asked
get one when it ends: applied, rejected, failed or expired.

"Could decide it" is the same three walls `approve_action` puts up, asked ahead
of time. An approver barred by separation of duties, or who cannot read the
target, or who lacks the right the apply needs, is not told: telling them only
invites a decision they cannot make.

Notifications ride beside the decision, never inside it. A failure to notify is
an Error Log, and the decision stands.
"""

from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, now_datetime

from frappe_agents.actions import (
	MAX_NOTIFIED_APPROVERS,
	approve_action,
	eligible_approvers,
	expire_stale_actions,
	notify_approvers,
	reject_action,
)
from frappe_agents.tests.fixtures import (
	APPROVER_USER,
	BLIND_APPROVER,
	DRAFT_USER,
	ORDER_DT,
	OWNED_AGENT,
	WEAK_APPROVER,
	AgentTestCase,
	as_user,
	make_action,
	make_order_draft,
	make_proposal,
	make_run,
)

REASON_MARKER = "ZQX-reason-marker"
AMOUNT_MARKER = 48213


def notices(action: str, user: str | None = None) -> list[dict]:
	filters = {"document_type": "Agent Action", "document_name": action}
	if user:
		filters["for_user"] = user
	return frappe.get_all(
		"Notification Log",
		filters=filters,
		fields=["for_user", "from_user", "subject", "email_content", "type"],
		order_by="creation asc",
	)


def recipients(action: str) -> set[str]:
	return {row.for_user for row in notices(action)}


class TestApproverNotifications(AgentTestCase):
	def test_an_eligible_approver_is_told(self):
		order = make_order_draft(user=DRAFT_USER)
		action = make_proposal(order.name, user=DRAFT_USER)

		rows = notices(action, APPROVER_USER)

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].type, "Alert")
		self.assertEqual(rows[0].from_user, DRAFT_USER)
		self.assertIn(order.name, rows[0].subject)

	def test_the_requester_is_not_told(self):
		order = make_order_draft(user=DRAFT_USER)
		action = make_proposal(order.name, user=DRAFT_USER)

		self.assertNotIn(DRAFT_USER, recipients(action))

	def test_an_approver_who_requested_it_is_not_told(self):
		order = make_order_draft(user=APPROVER_USER)
		action = make_proposal(order.name, user=APPROVER_USER)

		self.assertNotIn(APPROVER_USER, recipients(action))

	def test_the_agent_owner_is_not_told(self):
		order = make_order_draft(user=DRAFT_USER)
		action = make_proposal(order.name, user=DRAFT_USER, agent=OWNED_AGENT)

		self.assertNotIn(APPROVER_USER, recipients(action))

	def test_the_runs_effective_user_is_not_told(self):
		order = make_order_draft(user=DRAFT_USER)
		run = make_run(effective_user=APPROVER_USER)
		action = make_action(run=run, requested_by=DRAFT_USER, target_name=order.name)

		notify_approvers(action.name)

		self.assertNotIn(APPROVER_USER, recipients(action.name))

	def test_an_approver_who_cannot_read_the_target_is_not_told(self):
		order = make_order_draft(user=DRAFT_USER)
		action = make_proposal(order.name, user=DRAFT_USER)

		self.assertNotIn(BLIND_APPROVER, recipients(action))

	def test_an_approver_who_cannot_submit_the_target_is_not_told(self):
		order = make_order_draft(user=DRAFT_USER)
		action = make_proposal(order.name, user=DRAFT_USER)

		self.assertNotIn(WEAK_APPROVER, recipients(action))

	def test_the_subject_holds_no_field_values_and_no_reason(self):
		order = make_order_draft(user=DRAFT_USER, amount=AMOUNT_MARKER)
		action = make_proposal(order.name, user=DRAFT_USER, reason=f"Approve it. {REASON_MARKER}")

		rows = notices(action, APPROVER_USER)
		self.assertEqual(len(rows), 1)
		text = f"{rows[0].subject} {rows[0].email_content or ''}"
		self.assertNotIn(REASON_MARKER, text)
		self.assertNotIn(str(AMOUNT_MARKER), text)
		self.assertNotIn("FA Widget", text)

	def test_the_list_is_capped_and_sorted(self):
		order = make_order_draft(user=DRAFT_USER)
		action = frappe._dict(
			action_type="Submit", target_doctype=ORDER_DT, target_name=order.name, run=None, agent=None
		)
		many = [f"fa-cap-{index:02d}@example.com" for index in reversed(range(MAX_NOTIFIED_APPROVERS + 5))]

		with (
			patch("frappe_agents.actions.get_users_with_role", return_value=many),
			patch("frappe_agents.actions.separation_of_duties_block", return_value=None),
			patch("frappe.has_permission", return_value=True),
		):
			chosen = eligible_approvers(action, order)

		self.assertEqual(chosen, sorted(many)[:MAX_NOTIFIED_APPROVERS])

	def test_a_decided_proposal_is_not_announced(self):
		order = make_order_draft(user=DRAFT_USER)
		action = make_action(status="Rejected", requested_by=DRAFT_USER, target_name=order.name)

		notify_approvers(action.name)

		self.assertEqual(notices(action.name), [])

	def test_a_failed_notification_leaves_the_proposal_standing(self):
		order = make_order_draft(user=DRAFT_USER)

		with patch("frappe_agents.actions.make_notification_logs", side_effect=RuntimeError("redis down")):
			action = make_proposal(order.name, user=DRAFT_USER)

		self.assertEqual(frappe.db.get_value("Agent Action", action, "status"), "Pending")
		self.assertEqual(notices(action), [])
		self.assertTrue(
			frappe.db.exists("Error Log", {"method": "frappe_agents: approver notification failed"})
		)


class TestRequesterNotifications(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.order = make_order_draft(user=DRAFT_USER)
		self.action = make_proposal(self.order.name, user=DRAFT_USER)

	def modified(self):
		return frappe.db.get_value(ORDER_DT, self.order.name, "modified")

	def requester_notices(self) -> list[dict]:
		return notices(self.action, DRAFT_USER)

	def test_an_approval_tells_the_requester(self):
		with as_user(APPROVER_USER):
			approve_action(self.action, self.modified())

		rows = self.requester_notices()
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].from_user, APPROVER_USER)
		self.assertIn("approved", rows[0].subject)
		self.assertIn(self.order.name, rows[0].subject)

	def test_a_rejection_tells_the_requester_and_carries_the_note(self):
		with as_user(APPROVER_USER):
			reject_action(self.action, note="The quotation is not signed yet.")

		rows = self.requester_notices()
		self.assertEqual(len(rows), 1)
		self.assertIn("rejected", rows[0].subject)
		self.assertIn("The quotation is not signed yet.", rows[0].email_content)

	def test_a_failed_approval_tells_the_requester(self):
		# Submitted behind the proposal's back, so the apply finds no draft.
		order = frappe.get_doc(ORDER_DT, self.order.name)
		order.submit()

		with as_user(APPROVER_USER):
			result = approve_action(self.action, self.modified())

		self.assertEqual(result["status"], "Failed")
		rows = self.requester_notices()
		self.assertEqual(len(rows), 1)
		self.assertIn("failed", rows[0].subject)

	def test_the_decider_is_not_told(self):
		with as_user(APPROVER_USER):
			reject_action(self.action, note="No.")

		# The approver's notice is the one from the proposal; the decision adds none.
		self.assertEqual(len(notices(self.action, APPROVER_USER)), 1)

	def test_a_separate_effective_user_is_told_too(self):
		run = make_run(effective_user=WEAK_APPROVER)
		action = make_action(run=run, requested_by=DRAFT_USER, target_name=self.order.name)

		with as_user(APPROVER_USER):
			reject_action(action.name, note="Not this one.")

		self.assertEqual(recipients(action.name), {DRAFT_USER, WEAK_APPROVER})

	def test_a_failed_notification_does_not_undo_an_approval(self):
		with (
			patch(
				"frappe_agents.actions.enqueue_create_notification", side_effect=RuntimeError("redis down")
			),
			as_user(APPROVER_USER),
		):
			result = approve_action(self.action, self.modified())

		self.assertEqual(result["status"], "Applied")
		self.assertEqual(frappe.db.get_value("Agent Action", self.action, "status"), "Applied")
		self.assertEqual(frappe.db.get_value(ORDER_DT, self.order.name, "docstatus"), 1)
		self.assertEqual(self.requester_notices(), [])
		self.assertTrue(
			frappe.db.exists("Error Log", {"method": "frappe_agents: requester notification failed"})
		)

	def test_the_decision_is_published_to_the_requester(self):
		with patch("frappe.publish_realtime") as publish, as_user(APPROVER_USER):
			reject_action(self.action, note="No.")

		decided = [c for c in publish.call_args_list if c.args and c.args[0] == "agent_action_decided"]
		self.assertEqual(len(decided), 1)
		self.assertEqual(decided[0].kwargs["user"], DRAFT_USER)
		self.assertEqual(decided[0].args[1]["status"], "Rejected")
		self.assertEqual(decided[0].args[1]["action"], self.action)


class TestExpiryNotifications(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		frappe.db.set_single_value("Agent Settings", "action_expiry_days", 7)
		frappe.clear_document_cache("Agent Settings", "Agent Settings")
		expire_stale_actions()
		self.order = make_order_draft(user=DRAFT_USER)
		self.action = make_proposal(self.order.name, user=DRAFT_USER)
		frappe.db.set_value(
			"Agent Action",
			self.action,
			"creation",
			add_to_date(now_datetime(), days=-30),
			update_modified=False,
		)

	def test_expiry_tells_the_requester(self):
		self.assertEqual(expire_stale_actions(), 1)

		rows = notices(self.action, DRAFT_USER)
		self.assertEqual(len(rows), 1)
		self.assertIn("expired", rows[0].subject)
		self.assertFalse(rows[0].from_user)

	def test_a_second_sweep_tells_nobody_again(self):
		expire_stale_actions()
		before = len(notices(self.action))

		self.assertEqual(expire_stale_actions(), 0)

		self.assertEqual(len(notices(self.action)), before)
		self.assertEqual(frappe.db.get_value("Agent Action", self.action, "status"), "Expired")
