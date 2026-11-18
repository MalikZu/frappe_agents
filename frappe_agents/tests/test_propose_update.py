# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Proposing a change to a live record, and applying it as somebody else.

A live record has no submit step: it is the record the business works from from
the moment it is saved. `propose_update` records the new values and the values
the record held when the agent looked; a different person approves, and the
change is saved under that person's permissions.

Two framework behaviours shape these tests:

* `save()` does not refuse a field the user may not write at a higher permlevel.
  It puts the old value back and saves the rest. So the permlevel test asserts
  that *nothing* changed, not that something raised.
* Administrator skips that reset entirely, and `has_permission` is always True for
  it. Nothing here approves as Administrator; a permission test that did would
  pass on a broken gate.
"""

from contextlib import contextmanager
from unittest.mock import patch

import frappe

from frappe_agents.actions import (
	approve_action,
	eligible_approvers,
	expire_stale_actions,
	get_update_diff,
	reject_action,
)
from frappe_agents.field_changes import normalise
from frappe_agents.tests.fixtures import (
	APPROVER_USER,
	AUDITOR_USER,
	BLIND_APPROVER,
	CASE_DT,
	DRAFT_USER,
	IBAN_FIELD,
	ORDER_DT,
	PASSWORD_FIELD,
	PROJECT_ALPHA,
	PROJECT_BETA,
	PROJECT_DT,
	RATE_APPROVER,
	RATE_READING_APPROVER,
	RESTRICTED_USER,
	WEAK_APPROVER,
	WORKFLOW_ACTION,
	WORKFLOW_APPROVED_STATE,
	WORKFLOW_DRAFT_STATE,
	WORKFLOW_NAME,
	AgentTestCase,
	_drop_workflow,
	_ensure_workflow_masters,
	as_user,
	call_tool,
	make_case,
	make_matrix_agent,
	make_order_draft,
	make_submitted_order,
	rule,
	set_kill_switch,
)
from frappe_agents.tests.test_action_notifications import notices

REASON = "The customer confirmed it by phone this morning."
SECRET_RATE = "rate-9431"


def rewrite_priority(doc, method=None):
	"""A controller with its own idea of the priority, for the read-back test."""
	doc.priority = "Low"


def refuse_quoting_the_rate(doc, method=None):
	"""A controller whose refusal quotes a permlevel-1 value, as real ones do."""
	frappe.throw(f"Rate {doc.internal_rate} is not on the price list.")


@contextmanager
def case_workflow(allow_edit: str, state_field: str = "workflow_state"):
	"""An active Workflow on FA Test Case whose only states are editable by `allow_edit`.

	Named like the fixture Workflow on purpose: the base setUp purges that name
	with a commit, so a run that crashes inside this block cannot leave it behind.
	"""
	_ensure_workflow_masters()
	_drop_workflow(CASE_DT)
	workflow = frappe.get_doc(
		{
			"doctype": "Workflow",
			"workflow_name": WORKFLOW_NAME,
			"document_type": CASE_DT,
			"is_active": 1,
			"workflow_state_field": state_field,
			"states": [
				{"state": WORKFLOW_DRAFT_STATE, "doc_status": "0", "allow_edit": allow_edit},
				{"state": WORKFLOW_APPROVED_STATE, "doc_status": "0", "allow_edit": allow_edit},
			],
			"transitions": [
				{
					"state": WORKFLOW_DRAFT_STATE,
					"action": WORKFLOW_ACTION,
					"next_state": WORKFLOW_APPROVED_STATE,
					"allowed": allow_edit,
					"allow_self_approval": 1,
				}
			],
		}
	)
	workflow.flags.ignore_permissions = True
	workflow.insert(ignore_permissions=True)
	frappe.cache.hdel("workflow", CASE_DT)
	frappe.clear_document_cache("Workflow", WORKFLOW_NAME)
	try:
		yield workflow
	finally:
		_drop_workflow(CASE_DT)


class TestProposeUpdate(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		# A crashed workflow test leaves its cached name behind for this doctype.
		frappe.cache.hdel("workflow", CASE_DT)
		frappe.clear_cache(doctype=CASE_DT)

	def propose(self, name: str, values: dict, user: str = DRAFT_USER, doctype: str = CASE_DT, agent=None):
		extra = {"agent": agent} if agent else {}
		payload, _ = call_tool(
			user,
			"propose_update",
			{"doctype": doctype, "name": name, "values": values, "reason": REASON},
			**extra,
		)
		return payload

	def backdate(self, action: str, days: int = 30) -> None:
		created = frappe.utils.add_days(frappe.utils.now_datetime(), -days)
		frappe.db.set_value("Agent Action", action, "creation", created, update_modified=False)

	def proposed_by(self, agent: str, name: str, values: dict) -> str:
		payload = self.propose(name, values, agent=agent)
		self.assertTrue(payload["ok"], payload["error"])
		return payload["result"]["action"]

	def proposed(self, name: str, values: dict, user: str = DRAFT_USER) -> str:
		payload = self.propose(name, values, user=user)
		self.assertTrue(payload["ok"], payload["error"])
		return payload["result"]["action"]

	def approve(self, action: str, user: str = APPROVER_USER, doctype: str = CASE_DT) -> dict:
		target = frappe.db.get_value("Agent Action", action, "target_name")
		with as_user(user):
			return approve_action(action, frappe.db.get_value(doctype, target, "modified"))

	def value(self, name: str, fieldname: str):
		return frappe.db.get_value(CASE_DT, name, fieldname)

	def pending_updates(self, name: str) -> int:
		return frappe.db.count(
			"Agent Action", {"target_name": name, "action_type": "Update", "status": "Pending"}
		)

	# -- proposing -----------------------------------------------------------

	def test_a_proposal_records_the_new_and_the_seen_values_and_changes_nothing(self):
		case = make_case(hours=1.5)

		payload = self.propose(case.name, {"status": "Working", "hours": "3", "urgent": True})

		self.assertTrue(payload["ok"], payload["error"])
		action = frappe.get_doc("Agent Action", payload["result"]["action"])
		self.assertEqual(action.action_type, "Update")
		self.assertEqual(action.status, "Pending")
		self.assertEqual(action.requested_by, DRAFT_USER)
		self.assertEqual(
			frappe.parse_json(action.proposed_values), {"status": "Working", "hours": 3.0, "urgent": 1}
		)
		self.assertEqual(frappe.parse_json(action.seen_values), {"status": "Open", "hours": 1.5, "urgent": 0})
		self.assertEqual([change["value"] for change in payload["result"]["changes"]], ["Working", 3.0, 1])
		self.assertEqual(self.value(case.name, "status"), "Open")

	def test_a_record_with_a_submit_step_is_refused(self):
		"""Drafts go through update_draft; submitted documents through cancel and amend."""
		for order in (make_order_draft(user=DRAFT_USER), make_submitted_order()):
			payload = self.propose(order.name, {"amount": 5}, doctype=ORDER_DT)
			self.assertFalse(payload["ok"])
			self.assertIn("submit step", payload["error"])
		self.assertFalse(
			frappe.db.exists("Agent Action", {"action_type": "Update", "target_doctype": ORDER_DT})
		)

	def test_fields_a_proposal_may_not_touch_refuse_the_whole_call(self):
		case = make_case(external_id="EXT-1")
		refused = {
			"hidden_code": "not editable",
			"closing_note": "not editable",
			"external_id": "only be set once",
			IBAN_FIELD: "sensitive",
			PASSWORD_FIELD: "Password",
			"reference": "not editable",
			"details": "Text Editor",
			"lines": "Table",
			"owner": "system field",
			"no_such_field": "has no field",
		}
		for fieldname, words in refused.items():
			payload = self.propose(case.name, {"notes": "Called back.", fieldname: "x"})
			self.assertFalse(payload["ok"], fieldname)
			self.assertIn(words, payload["error"], fieldname)
		self.assertEqual(self.pending_updates(case.name), 0)

	def test_a_field_the_run_user_may_not_read_is_refused_without_its_value(self):
		case = make_case(internal_rate=SECRET_RATE)

		payload = self.propose(case.name, {"internal_rate": "rate-1"}, user=RESTRICTED_USER)

		self.assertFalse(payload["ok"])
		self.assertIn("not allowed to read internal_rate", payload["error"])
		self.assertNotIn(SECRET_RATE, payload["error"])

	def test_an_agent_without_the_read_grant_is_refused(self):
		"""Otherwise "already holds that value" would read a field one guess at a time.

		Read on another doctype offers the tool; the call on this one still needs
		Read on this one.
		"""
		agent = make_matrix_agent([rule(CASE_DT, can_propose=1), rule(PROJECT_DT, can_read=1, can_propose=1)])
		case = make_case(notes="Called back.")

		payload = self.propose(case.name, {"notes": "Called back."}, agent=agent.name)

		self.assertFalse(payload["ok"])
		self.assertIn(f"read {CASE_DT}", payload["error"])
		self.assertNotIn("already hold", payload["error"])

	def test_a_link_to_a_doctype_the_agent_may_not_read_is_resolved_at_approval(self):
		"""No lookup on an ungranted doctype at proposal time; the approver's check decides."""
		agent = make_matrix_agent([rule(CASE_DT, can_read=1, can_propose=1)])
		case = make_case()
		case.db_set("project", None)

		action = self.proposed_by(agent.name, case.name, {"project": PROJECT_ALPHA.upper()})
		stored = frappe.parse_json(frappe.db.get_value("Agent Action", action, "proposed_values"))
		self.assertEqual(stored["project"], PROJECT_ALPHA.upper())

		self.assertEqual(self.approve(action)["status"], "Applied")
		self.assertEqual(self.value(case.name, "project"), PROJECT_ALPHA)

	def test_a_read_only_doctype_is_refused(self):
		case = make_case()
		frappe.make_property_setter(
			{"doctype": CASE_DT, "doctype_or_field": "DocType", "property": "read_only", "value": 1},
			validate_fields_for_doctype=False,
		)
		self.addCleanup(frappe.clear_cache, doctype=CASE_DT)
		frappe.clear_cache(doctype=CASE_DT)

		payload = self.propose(case.name, {"notes": "Checked."})

		self.assertFalse(payload["ok"])
		self.assertIn("is read-only", payload["error"])

	def test_too_many_fields_are_refused(self):
		values = {f"field_{index}": "x" for index in range(21)}
		payload = self.propose(make_case().name, values)
		self.assertFalse(payload["ok"])
		self.assertIn("Too many fields", payload["error"])

	def test_a_value_longer_than_its_column_is_refused(self):
		payload = self.propose(make_case().name, {"contact": "x" * 141})
		self.assertFalse(payload["ok"])
		self.assertIn("too long", payload["error"])

	def test_a_sixth_pending_change_is_refused(self):
		case = make_case()
		for values in (
			{"status": "Working"},
			{"priority": "High"},
			{"hours": 2},
			{"due_date": "2026-12-01"},
			{"urgent": 1},
		):
			self.proposed(case.name, values)

		payload = self.propose(case.name, {"contact": "Dana"})

		self.assertFalse(payload["ok"])
		self.assertIn("5 pending field changes", payload["error"])

	def test_a_pending_change_past_its_window_blocks_nothing(self):
		"""The sweep has not marked it Expired yet, but it can no longer be approved."""
		case = make_case()
		stale = self.proposed(case.name, {"priority": "High"})
		self.backdate(stale)

		payload = self.propose(case.name, {"priority": "Medium"})

		self.assertTrue(payload["ok"], payload["error"])

	def test_values_that_already_hold_are_refused(self):
		"""`"2.50"` is 2.5 and `"1"` is a ticked box: no change, so no proposal."""
		case = make_case(hours=2.5, urgent=1, due_date="2026-12-01")

		payload = self.propose(case.name, {"hours": "2.50", "urgent": "1", "due_date": "2026-12-01"})

		self.assertFalse(payload["ok"])
		self.assertIn("already hold", payload["error"])

	def test_values_of_the_wrong_kind_are_refused(self):
		case = make_case()
		for values in ({"hours": "a lot"}, {"urgent": "maybe"}, {"due_date": "soon"}, {"status": "Lost"}):
			payload = self.propose(case.name, values)
			self.assertFalse(payload["ok"], values)
		self.assertEqual(self.pending_updates(case.name), 0)

	def test_a_link_is_stored_as_the_records_own_name(self):
		case = make_case()
		case.db_set("project", None)

		action = self.proposed(case.name, {"project": PROJECT_ALPHA.upper()})

		proposed = frappe.parse_json(frappe.db.get_value("Agent Action", action, "proposed_values"))
		self.assertEqual(proposed["project"], PROJECT_ALPHA)

	def test_a_link_the_run_user_cannot_read_is_refused(self):
		case = make_case()

		payload = self.propose(case.name, {"project": PROJECT_BETA}, user=RESTRICTED_USER)

		self.assertFalse(payload["ok"])
		self.assertIn("you can read", payload["error"])

	def test_overlapping_proposals_are_refused_and_disjoint_ones_both_apply(self):
		case = make_case()
		first = self.proposed(case.name, {"status": "Working"})

		overlap = self.propose(case.name, {"status": "Closed", "notes": "Done."})
		self.assertFalse(overlap["ok"])
		self.assertIn(first, overlap["error"])

		second = self.proposed(case.name, {"priority": "High"})

		self.assertEqual(self.approve(first)["status"], "Applied")
		self.assertEqual(self.approve(second)["status"], "Applied")
		self.assertEqual(self.value(case.name, "status"), "Working")
		self.assertEqual(self.value(case.name, "priority"), "High")

	# -- approving -----------------------------------------------------------

	def test_an_approved_change_is_saved_as_the_approver(self):
		case = make_case()
		action = self.proposed(
			case.name, {"status": "Closed", "hours": 4.25, "due_date": "2026-12-24", "urgent": 1}
		)

		result = self.approve(action)

		self.assertEqual(result["status"], "Applied")
		row = frappe.get_doc("Agent Action", action)
		self.assertEqual(row.decided_by, APPROVER_USER)
		self.assertEqual(row.edited_before_approval, 0)
		saved = frappe.get_doc(CASE_DT, case.name)
		self.assertEqual(saved.status, "Closed")
		self.assertEqual(saved.hours, 4.25)
		self.assertEqual(str(saved.due_date), "2026-12-24")
		self.assertEqual(saved.urgent, 1)
		self.assertEqual(saved.modified_by, APPROVER_USER)

	def test_the_requester_cannot_approve_their_own_change(self):
		case = make_case()
		action = self.proposed(case.name, {"status": "Working"}, user=APPROVER_USER)

		with self.assertRaises(frappe.PermissionError):
			self.approve(action)

		self.assertEqual(self.value(case.name, "status"), "Open")

	def test_an_approver_without_write_fails_the_action(self):
		case = make_case()
		action = self.proposed(case.name, {"status": "Working"})

		result = self.approve(action, user=WEAK_APPROVER)

		self.assertEqual(result["status"], "Failed")
		self.assertIn("not allowed to change", result["failure"])
		self.assertEqual(self.value(case.name, "status"), "Open")

	def test_write_through_a_share_is_enough(self):
		"""Level-0 access is the document's own right, and a share grants it too."""
		case = make_case()
		action = self.proposed(case.name, {"status": "Working"})
		frappe.share.add_docshare(
			CASE_DT, case.name, WEAK_APPROVER, read=1, write=1, flags={"ignore_share_permission": True}
		)

		result = self.approve(action, user=WEAK_APPROVER)

		self.assertEqual(result["status"], "Applied", result.get("failure"))
		self.assertEqual(self.value(case.name, "status"), "Working")

	def test_an_approver_who_may_not_read_a_field_cannot_approve_it(self):
		"""Approving a field you cannot see is approving blind."""
		case = make_case(internal_rate=SECRET_RATE)
		action = self.proposed(case.name, {"notes": "Re-rated.", "internal_rate": "rate-1"})

		with as_user(APPROVER_USER):
			self.assertEqual(get_update_diff(action)["unreadable"], 1)
		result = self.approve(action)

		self.assertEqual(result["status"], "Failed")
		self.assertIn("not allowed to read Internal Rate", result["failure"])
		self.assertEqual(self.value(case.name, "internal_rate"), SECRET_RATE)
		self.assertFalse(self.value(case.name, "notes"))

	def test_an_approver_who_may_not_write_one_field_changes_nothing(self):
		"""save() would quietly keep the old rate and write the notes. Neither happens."""
		case = make_case(internal_rate=SECRET_RATE)
		before = self.value(case.name, "modified")
		action = self.proposed(case.name, {"notes": "Re-rated.", "internal_rate": "rate-1"})

		result = self.approve(action, user=RATE_READING_APPROVER)

		self.assertEqual(result["status"], "Failed")
		self.assertIn("not allowed to change Internal Rate", result["failure"])
		self.assertNotIn("did not keep", result["failure"])
		self.assertNotIn(SECRET_RATE, result["failure"])
		self.assertEqual(self.value(case.name, "internal_rate"), SECRET_RATE)
		self.assertFalse(self.value(case.name, "notes"))
		self.assertEqual(self.value(case.name, "modified"), before)

	def test_an_approver_who_may_write_a_permlevel_field_changes_it(self):
		case = make_case(internal_rate=SECRET_RATE)
		action = self.proposed(case.name, {"internal_rate": "rate-1"})

		told = eligible_approvers(frappe.get_doc("Agent Action", action), frappe.get_doc(CASE_DT, case.name))
		result = self.approve(action, user=RATE_APPROVER)

		self.assertIn(RATE_APPROVER, told)
		self.assertNotIn(RATE_READING_APPROVER, told)
		self.assertNotIn(APPROVER_USER, told)
		self.assertEqual(result["status"], "Applied", result.get("failure"))
		self.assertEqual(self.value(case.name, "internal_rate"), "rate-1")

	def test_a_link_the_approver_may_not_read_fails_the_action(self):
		"""RATE_APPROVER edits cases and reads no project at all."""
		case = make_case()
		action = self.proposed(case.name, {"project": PROJECT_BETA})

		result = self.approve(action, user=RATE_APPROVER)

		self.assertEqual(result["status"], "Failed")
		self.assertIn("does not exist or that you are not allowed to read", result["failure"])
		self.assertEqual(self.value(case.name, "project"), PROJECT_ALPHA)

	def test_a_missing_link_reads_the_same_as_an_unreadable_one(self):
		"""Two answers would tell an approver which records exist where they cannot read."""
		agent = make_matrix_agent([rule(CASE_DT, can_read=1, can_propose=1)])
		case = make_case()
		# Kept as written: the agent may not read projects, so nothing looks it up.
		action = self.proposed_by(agent.name, case.name, {"project": "FA Nowhere"})

		result = self.approve(action)

		self.assertEqual(result["status"], "Failed")
		self.assertIn("does not exist or that you are not allowed to read", result["failure"])
		self.assertEqual(self.value(case.name, "project"), PROJECT_ALPHA)

	def test_a_save_refusal_is_recorded_without_its_values(self):
		"""The controller quotes the rate; the row and the requester must not."""
		case = make_case(internal_rate=SECRET_RATE)
		action = self.proposed(case.name, {"notes": "Escalated."})

		hooks = dict(frappe.get_doc_hooks())
		hooks[CASE_DT] = {"validate": [f"{__name__}.refuse_quoting_the_rate"]}
		with patch.object(frappe.local, "doc_events_hooks", hooks):
			result = self.approve(action)

		self.assertEqual(result["status"], "Failed")
		self.assertIn(SECRET_RATE, result["detail"])
		self.assertNotIn(SECRET_RATE, result["failure"])
		self.assertNotIn(SECRET_RATE, frappe.db.get_value("Agent Action", action, "failure"))
		self.assertTrue(all(SECRET_RATE not in row.subject for row in notices(action)))
		self.assertFalse(self.value(case.name, "notes"))

	def test_a_proposed_field_that_moved_since_fails_the_action(self):
		case = make_case()
		action = self.proposed(case.name, {"priority": "High"})
		frappe.get_doc(CASE_DT, case.name).update({"priority": "Medium"}).save(ignore_permissions=True)

		result = self.approve(action)

		self.assertEqual(result["status"], "Failed")
		self.assertIn("Priority changed after the agent proposed this", result["failure"])
		self.assertEqual(self.value(case.name, "priority"), "Medium")

	def test_an_unrelated_edit_does_not_block_and_is_recorded(self):
		case = make_case()
		action = self.proposed(case.name, {"priority": "High"})
		frappe.get_doc(CASE_DT, case.name).update({"notes": "Customer called."}).save(ignore_permissions=True)

		result = self.approve(action)

		self.assertEqual(result["status"], "Applied")
		self.assertEqual(result["edited_before_approval"], 1)
		self.assertEqual(self.value(case.name, "priority"), "High")
		self.assertEqual(self.value(case.name, "notes"), "Customer called.")

	def test_a_value_the_save_did_not_keep_rolls_the_change_back(self):
		case = make_case()
		before = self.value(case.name, "modified")
		action = self.proposed(case.name, {"priority": "High", "notes": "Escalated."})

		hooks = dict(frappe.get_doc_hooks())
		hooks[CASE_DT] = {"validate": [f"{__name__}.rewrite_priority"]}
		with patch.object(frappe.local, "doc_events_hooks", hooks):
			result = self.approve(action)

		self.assertEqual(result["status"], "Failed")
		self.assertIn("did not keep the approved value", result["failure"])
		self.assertEqual(self.value(case.name, "priority"), "Low")
		self.assertFalse(self.value(case.name, "notes"))
		self.assertEqual(self.value(case.name, "modified"), before)

	def test_a_field_that_turned_read_only_since_fails_the_action(self):
		case = make_case()
		action = self.proposed(case.name, {"notes": "Checked."})
		frappe.make_property_setter(
			{"doctype": CASE_DT, "fieldname": "notes", "property": "read_only", "value": 1},
			validate_fields_for_doctype=False,
		)
		self.addCleanup(frappe.clear_cache, doctype=CASE_DT)
		frappe.clear_cache(doctype=CASE_DT)

		result = self.approve(action)

		self.assertEqual(result["status"], "Failed")
		self.assertIn("not editable", result["failure"])
		self.assertFalse(self.value(case.name, "notes"))

	def test_the_kill_switch_stops_the_apply(self):
		case = make_case()
		action = self.proposed(case.name, {"status": "Working"})
		set_kill_switch(0)
		self.addCleanup(set_kill_switch, 1)

		with self.assertRaises(frappe.ValidationError):
			self.approve(action)

		self.assertEqual(frappe.db.get_value("Agent Action", action, "status"), "Pending")
		self.assertEqual(self.value(case.name, "status"), "Open")

	# -- workflows -----------------------------------------------------------

	def test_a_workflow_state_field_is_refused(self):
		"""Under a name of the Workflow's choosing, not only the conventional one."""
		case = make_case()
		with case_workflow("System Manager", state_field="case_stage"):
			payload = self.propose(case.name, {"case_stage": WORKFLOW_APPROVED_STATE})
		self.assertFalse(payload["ok"])
		self.assertIn("is the workflow state", payload["error"])
		self.assertIn("propose_transition", payload["error"])

	def test_an_approver_without_the_states_edit_role_fails_the_action(self):
		case = make_case()
		with case_workflow("System Manager"):
			action = self.proposed(case.name, {"priority": "High"})
			told = eligible_approvers(
				frappe.get_doc("Agent Action", action), frappe.get_doc(CASE_DT, case.name)
			)
			result = self.approve(action)

		self.assertNotIn(APPROVER_USER, told)
		self.assertEqual(result["status"], "Failed")
		self.assertIn("only the System Manager role may edit", result["failure"])
		self.assertNotIn("submit or cancel", result["failure"])
		self.assertEqual(self.value(case.name, "priority"), "Low")

	def test_an_approver_with_the_states_edit_role_applies_the_change(self):
		from frappe_agents.tests.fixtures import SUBMITTER_ROLE

		case = make_case()
		with case_workflow(SUBMITTER_ROLE):
			action = self.proposed(case.name, {"priority": "High"})
			result = self.approve(action)

		self.assertEqual(result["status"], "Applied", result.get("failure"))
		self.assertEqual(self.value(case.name, "priority"), "High")

	# -- what the approver sees ----------------------------------------------

	def test_the_diff_shows_before_now_and_proposed(self):
		case = make_case()
		action = self.proposed(case.name, {"priority": "High", "internal_rate": "rate-1"})

		with as_user(APPROVER_USER):
			diff = get_update_diff(action)

		self.assertEqual(diff["target_readable"], 1)
		rows = {row["fieldname"]: row for row in diff["fields"]}
		self.assertEqual(
			(rows["priority"]["before"], rows["priority"]["now"], rows["priority"]["proposed"]),
			("Low", "Low", "High"),
		)
		# The approver holds no read at permlevel 1: the label, and nothing else.
		self.assertEqual(rows["internal_rate"]["readable"], 0)
		self.assertNotIn("proposed", rows["internal_rate"])

	def test_the_diff_marks_a_field_that_moved(self):
		case = make_case()
		action = self.proposed(case.name, {"priority": "High"})
		frappe.get_doc(CASE_DT, case.name).update({"priority": "Medium"}).save(ignore_permissions=True)

		with as_user(APPROVER_USER):
			diff = get_update_diff(action)

		self.assertEqual(diff["changed_since"], 1)
		self.assertEqual(diff["fields"][0]["now"], "Medium")

	def test_a_reader_who_cannot_read_the_target_gets_no_values(self):
		"""BLIND_APPROVER may read the Agent Action but not the case it is about."""
		case = make_case(internal_rate=SECRET_RATE)
		action = self.proposed(case.name, {"priority": "High"})

		with as_user(BLIND_APPROVER):
			diff = get_update_diff(action)
			row = frappe.client.get("Agent Action", action)

		self.assertEqual(diff, {"fields": [], "target_readable": 0, "changed_since": 0})
		self.assertFalse(row.get("proposed_values"))
		self.assertFalse(row.get("seen_values"))

	def test_a_reader_of_the_queue_cannot_filter_on_the_stored_values(self):
		"""A `like` filter on the hidden column would read the values one guess at a time.

		frappe v16 refuses a filter on a field the user may not read at its
		permlevel. version-15 refuses sorting and grouping only, which is why the
		port there keeps the values out of Agent Action altogether.
		"""
		case = make_case(internal_rate=SECRET_RATE)
		self.proposed(case.name, {"internal_rate": "rate-1"})

		for column in ("seen_values", "proposed_values"):
			with as_user(AUDITOR_USER), self.assertRaises(frappe.PermissionError, msg=column):
				frappe.get_list("Agent Action", filters={column: ["like", "%rate%"]}, pluck="name")

	# -- who is told ----------------------------------------------------------

	def test_approvers_who_may_write_are_the_ones_told(self):
		case = make_case()
		action = frappe.get_doc("Agent Action", self.proposed(case.name, {"priority": "High"}))
		with_rate = frappe.get_doc(
			"Agent Action", self.proposed(make_case().name, {"internal_rate": "rate-1"})
		)

		told = eligible_approvers(action, frappe.get_doc(CASE_DT, case.name))
		self.assertIn(APPROVER_USER, told)
		self.assertNotIn(WEAK_APPROVER, told)
		self.assertNotIn(
			APPROVER_USER, eligible_approvers(with_rate, frappe.get_doc(CASE_DT, with_rate.target_name))
		)

		subject = frappe.db.get_value(
			"Notification Log", {"document_name": action.name, "for_user": APPROVER_USER}, "subject"
		)
		self.assertIn("proposes to change fields on", subject or "")

	def test_the_requester_is_told_how_a_change_ended(self):
		"""Through the same notices a submit gets, worded as a change."""
		applied = self.proposed(make_case().name, {"priority": "High"})
		failed = self.proposed(make_case().name, {"status": "Working"})

		self.approve(applied)
		self.approve(failed, user=WEAK_APPROVER)

		rejected = self.proposed(make_case().name, {"urgent": 1})
		with as_user(APPROVER_USER):
			reject_action(rejected, "Not this week.")
		expired = self.proposed(make_case().name, {"hours": 3})
		self.backdate(expired)
		expire_stale_actions()

		outcomes = (
			(applied, "approved the proposal to change fields on"),
			(failed, "failed"),
			(rejected, "rejected the proposal to change fields on"),
			(expired, "expired"),
		)
		for action, words in outcomes:
			rows = notices(action, DRAFT_USER)
			self.assertEqual(len(rows), 1, action)
			self.assertIn(words, rows[0].subject)
			self.assertIn("change fields on", rows[0].subject)


class TestNormalise(AgentTestCase):
	def df(self, fieldtype: str):
		return frappe._dict(fieldtype=fieldtype, fieldname="f")

	def test_equal_values_normalise_alike(self):
		pairs = (
			("Check", "1", True),
			("Int", "10", 10.0),
			("Float", "10", 10.0),
			("Date", "2026-11-18", frappe.utils.getdate("2026-11-18")),
			("Data", None, ""),
			("Data", "  padded ", "padded"),
			("Time", "09:30:00", frappe.utils.get_timedelta("09:30:00")),
		)
		for fieldtype, left, right in pairs:
			self.assertEqual(
				normalise(self.df(fieldtype), left), normalise(self.df(fieldtype), right), fieldtype
			)

	def test_a_float_keeps_the_decimals_the_column_keeps(self):
		"""Not the system's display precision: 3.6725 and 3.67251 are different values."""
		from frappe_agents.field_changes import STORED_DECIMALS

		df = self.df("Float")
		self.assertNotEqual(
			normalise(df, "3.6725", STORED_DECIMALS), normalise(df, "3.67251", STORED_DECIMALS)
		)

	def test_a_duration_keeps_its_fraction_of_a_second(self):
		df = self.df("Duration")
		self.assertEqual(normalise(df, "90.5", 9), 90.5)
		self.assertNotEqual(normalise(df, "90.5", 9), normalise(df, 90, 9))

	def test_a_word_is_not_a_number(self):
		from frappe_agents.field_changes import UnusableValue

		refused = (
			("Int", "ten"),
			("Check", "yes"),
			("Float", "nan"),
			("Date", "soon"),
			("Date", "2026-11-18garbage"),
			# A zone would be stored as text the column cannot hold, or mean another hour.
			("Datetime", "2026-11-18T10:00:00Z"),
			("Datetime", "2026-11-18 10:00:00+04:00"),
		)
		for fieldtype, value in refused:
			with self.assertRaises(UnusableValue, msg=fieldtype):
				normalise(self.df(fieldtype), value)
