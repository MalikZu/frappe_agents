"""The approval engine: what happens to an agent's proposal once a human sees it.

An Agent Action is a request, not an act. `approve_action` is where it becomes an
act, and it does so **as the approver** — `doc.submit()` and `doc.cancel()` run in
the approver's own session under the approver's own permissions. There is no
`ignore_permissions` on the apply path, deliberately: an approver who may not
submit the document may not cause it to be submitted either.

A Workflow Transition is applied the same way, through Frappe's own
`apply_workflow`, in the approver's session. The Workflow then decides: the
transition must be open to the approver's roles, its condition must hold, and
self-approval is refused where the transition says so. `apply_workflow` may
submit or cancel the document, which is why this module is the only place in
the app that calls it — no tool does.

Three things in here look like belt-and-braces and are not:

* **The workflow refusal is checked twice.** Frappe does not block a server-side
  `doc.submit()` on a doctype with an active Workflow — it teleports the document
  to the first submitted state and skips every approval on the way. The proposal
  tools refuse such a doctype, and this module refuses it again immediately before
  applying, because a Workflow can be switched on between the two moments.

* **`expected_modified` is the whole lock.** Frappe's own `TimestampMismatchError`
  compares against the timestamp the in-memory document was loaded with, so on a
  server-side path like this one it can never fire. Nothing catches a document that
  changed under the approver except the timestamp the approver sends back. Both
  operands go through `get_datetime` — `modified` is a datetime when loaded and a
  string right after a save, and `str()` of a datetime drops zero microseconds, so
  string equality is wrong in a way that shows up rarely and silently.

* **Nothing is committed between the apply and the status write.** Cancel validates
  back-links *after* the row is already written at docstatus 2, so a `LinkExistsError`
  must roll the document and the Agent Action back together. The apply runs inside a
  savepoint and this module never calls `frappe.db.commit()`.

A failed apply is a normal outcome, not a bug: `submit()` re-runs `validate()` in
full, so a draft that was valid when the agent built it can legitimately fail at
approval time. That is recorded on the row as Failed with the reason, never raised
into the approver's request as an unhandled error.
"""

import datetime
from contextlib import contextmanager
from typing import Any

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import (
	enqueue_create_notification,
	make_notification_logs,
)
from frappe.model.workflow import apply_workflow, get_workflow, get_workflow_name
from frappe.utils import (
	add_to_date,
	cint,
	escape_html,
	get_datetime,
	get_fullname,
	now_datetime,
	strip_html,
)
from frappe.utils.user import get_users_with_role

from frappe_agents.tools.base import runtime_enabled

APPROVER_ROLE = "Agent Approver"

STATUS_PENDING = "Pending"
STATUS_REJECTED = "Rejected"
STATUS_APPLIED = "Applied"
STATUS_FAILED = "Failed"
STATUS_EXPIRED = "Expired"

ACTION_SUBMIT = "Submit"
ACTION_CANCEL = "Cancel"
ACTION_TRANSITION = "Workflow Transition"

DRAFT = 0
SUBMITTED = 1
CANCELLED = 2

DEFAULT_EXPIRY_DAYS = 7
FAILURE_LIMIT = 500
NOTE_LIMIT = 1000
SAVEPOINT = "agent_action_apply"

# A proposal is a question for a few people, not a broadcast. Past this many
# eligible approvers the rest are not told; they still see it in the queue.
MAX_NOTIFIED_APPROVERS = 20
NOTIFICATION_TYPE = "Alert"
NOTIFY_SAVEPOINT = "agent_action_notify"
SUBJECT_NOTE_LIMIT = 140
# What a notification is built from. Never the reason, never the target's fields.
NOTICE_FIELDS = (
	"name",
	"status",
	"run",
	"agent",
	"requested_by",
	"action_type",
	"workflow_action",
	"workflow_name",
	"from_workflow_state",
	"target_doctype",
	"target_name",
)

# How many of a user's past rejections on one document the proposal tools look
# through for one made on the same version of it.
REJECTION_LOOKBACK = 5

# What a proposal's state is read from, for the chat card and the agent's history.
STATE_FIELDS = (
	"name",
	"run",
	"status",
	"action_type",
	"workflow_action",
	"workflow_name",
	"from_workflow_state",
	"target_doctype",
	"target_name",
	"reason",
	"decision_note",
	"failure",
	"decided_at",
	"requested_by",
	"agent",
	"creation",
)


@frappe.whitelist(methods=["POST"])
def approve_action(action: str, expected_modified: Any, note: str | None = None) -> dict:
	"""Approve one Agent Action and apply it as the approver.

	`expected_modified` is the target document's `modified` as it was on the screen
	the approver decided from. It is required, and a mismatch refuses the approval.

	It is annotated `Any` on purpose. `frappe.whitelist` validates arguments against
	their annotations, and `modified` is a `datetime` whenever it came out of the
	database and a `str` whenever it came off the wire — so pinning it to `str` would
	reject the value every server-side caller naturally has. `_check_lock` is what
	narrows it, through `get_datetime`, and it fails closed on anything unparseable.
	"""
	doc_action = _load_action(action)
	note = _clean_note(note)

	_check_kill_switch()
	_require_pending(doc_action)
	_check_expiry(doc_action)
	_require_separation_of_duties(doc_action)

	target = _load_target(doc_action)
	if target is None:
		return _record_failure(
			doc_action,
			note,
			0,
			_("{0} {1} no longer exists.").format(doc_action.target_doctype, doc_action.target_name),
		)

	_check_lock(doc_action, target, expected_modified)

	problem = _apply_problem(doc_action, target)
	if problem:
		return _record_failure(doc_action, note, 0, problem)

	# Before the apply, never after: submit() overwrites `modified` with the
	# approver's own timestamp, and the comparison stops meaning anything.
	edited = 0 if _same_timestamp(target.modified, doc_action.proposal_modified) else 1

	frappe.clear_messages()
	frappe.flags.error_message = None

	# Read again, immediately before the only line in this module that changes a
	# document. Everything between the first check and here is database work, and a
	# switch thrown during it has to stop the apply — the check that matters is the
	# last one before the write, not the first one after the request arrived.
	_check_kill_switch()

	frappe.db.savepoint(SAVEPOINT)
	try:
		if doc_action.action_type == ACTION_TRANSITION:
			# As the session user, who is the approver. apply_workflow lists the
			# transitions for the approver's roles, checks the condition and
			# self-approval, and saves, submits or cancels with their permissions.
			apply_workflow(target, doc_action.workflow_action)
		elif doc_action.action_type == ACTION_SUBMIT:
			target.submit()
		elif doc_action.action_type == ACTION_CANCEL:
			target.cancel()
		else:
			frappe.throw(_("Unknown action type {0}.").format(doc_action.action_type))
	except Exception as exc:
		# Undo whatever the half-finished transition wrote — a cancel that trips on a
		# back-link has already written docstatus 2 by the time it throws.
		frappe.db.rollback(save_point=SAVEPOINT)
		return _record_failure(doc_action, note, edited, _failure_text(exc))

	_write(
		doc_action,
		{
			"status": STATUS_APPLIED,
			"decided_by": frappe.session.user,
			"decided_at": now_datetime(),
			"decision_note": note,
			"edited_before_approval": edited,
			"applied_doc": f"{doc_action.target_doctype}: {doc_action.target_name}",
			"failure": None,
		},
	)
	_notify_requester(doc_action, STATUS_APPLIED)

	return {
		"action": doc_action.name,
		"status": STATUS_APPLIED,
		"target_doctype": doc_action.target_doctype,
		"target_name": doc_action.target_name,
		# Read back rather than taken from `target`: apply_workflow loads and saves
		# its own copy of the document.
		"docstatus": cint(
			frappe.db.get_value(doc_action.target_doctype, doc_action.target_name, "docstatus")
		),
		"edited_before_approval": edited,
	}


@frappe.whitelist(methods=["POST"])
def reject_action(action: str, note: str) -> dict:
	"""Reject one Agent Action. The note is required — the agent's answer is why."""
	doc_action = _load_action(action)
	note = _clean_note(note)
	if not note:
		frappe.throw(_("Say why you are rejecting this. The note is the answer to the proposal."))

	# No kill-switch check: clearing the queue is exactly what a site with the
	# runtime switched off should still be able to do.
	_require_pending(doc_action)
	_check_expiry(doc_action)
	_require_separation_of_duties(doc_action)

	# edited_before_approval is left alone on purpose: it measures what happened to a
	# document that was applied, and a rejection applied nothing.
	_write(
		doc_action,
		{
			"status": STATUS_REJECTED,
			"decided_by": frappe.session.user,
			"decided_at": now_datetime(),
			"decision_note": note,
		},
	)
	_notify_requester(doc_action, STATUS_REJECTED, note)

	return {"action": doc_action.name, "status": STATUS_REJECTED}


def pending_action_for(
	target_doctype: str,
	target_name: str,
	action_type: str,
	workflow_action: str | None = None,
	from_workflow_state: str | None = None,
) -> str | None:
	"""The Pending proposal already covering this target and action, if there is one.

	For a workflow transition the action is the workflow's own verb out of one
	state: a pending Approve does not block a Reject, and a stale Approve out of an
	earlier state does not block the Approve out of this one.

	A Pending row past its window does not count either. It cannot be approved
	any more, and the daily sweep has simply not written Expired on it yet.
	"""
	filters = {
		"target_doctype": target_doctype,
		"target_name": target_name,
		"action_type": action_type,
		"status": STATUS_PENDING,
		"creation": (">=", _cutoff(_expiry_days())),
	}
	if workflow_action:
		filters["workflow_action"] = workflow_action
	if from_workflow_state:
		filters["from_workflow_state"] = from_workflow_state
	return frappe.db.exists("Agent Action", filters)


def record_proposal(
	run: Any,
	action_type: str,
	target_doctype: str,
	target_name: str,
	reason: str,
	proposal_modified: Any,
	workflow_action: str | None = None,
	workflow_name: str | None = None,
	from_workflow_state: str | None = None,
) -> Any:
	"""Insert one Pending Agent Action for a proposal the tools have already checked.

	The row is written with `ignore_permissions` because Agent Action grants create
	to no role at all: proposals arrive through this function and leave through
	`approve_action`, `reject_action` or the expiry sweep, and through nothing else.
	It lives here rather than in the tools so that tool code stays free of it.
	"""
	action = frappe.get_doc(
		{
			"doctype": "Agent Action",
			"run": run.name,
			"agent": run.agent,
			"requested_by": frappe.session.user,
			"action_type": action_type,
			"workflow_action": workflow_action,
			"workflow_name": workflow_name,
			"from_workflow_state": from_workflow_state,
			"target_doctype": target_doctype,
			"target_name": target_name,
			"reason": reason,
			# The lock and the review metric both hang off this one snapshot, so it is
			# stored as a datetime — never a string, whose formatting is not stable.
			"proposal_modified": get_datetime(proposal_modified),
			"status": STATUS_PENDING,
		}
	)
	action.flags.ignore_permissions = True
	action.insert(ignore_permissions=True)
	_enqueue_approver_notice(action.name)
	return action


def expire_stale_actions() -> int:
	"""Expire proposals nobody decided. Daily job, and safe to run by hand.

	Idempotent by construction: it selects only Pending rows, so a second run in the
	same window matches nothing. It writes no decider because nobody decided, and it
	leaves `modified` alone because expiry is the clock, not an edit.
	"""
	days = _expiry_days()
	filters = {"status": STATUS_PENDING, "creation": ("<", _cutoff(days))}

	# Read before the bulk write: once it lands, nothing tells these rows apart from
	# the ones an earlier sweep expired, and those must not be announced twice.
	stale = frappe.get_all("Agent Action", filters=filters, fields=list(NOTICE_FIELDS))
	if not stale:
		return 0

	frappe.db.set_value(
		"Agent Action",
		{"name": ("in", [row.name for row in stale]), "status": STATUS_PENDING},
		{
			"status": STATUS_EXPIRED,
			"failure": _("Expired after {0} days with no decision.").format(days),
		},
		update_modified=False,
	)
	# Announce only what this sweep moved. A row decided between the read and the
	# write kept its status, and its requester hears about the decision instead.
	expired = set(
		frappe.get_all(
			"Agent Action",
			filters={"name": ("in", [row.name for row in stale]), "status": STATUS_EXPIRED},
			pluck="name",
		)
	)
	for row in stale:
		if row.name in expired:
			_notify_requester(row, STATUS_EXPIRED)
	return len(expired)


def separation_of_duties_block(action: Any, user: str | None = None) -> str | None:
	"""Why `user` may not decide this proposal, or None if they may.

	Three people are kept apart from the decision: whoever asked for it, whoever the
	run was acting as, and whoever owns the agent. An approval nobody independent
	made is not a review.
	"""
	user = user or frappe.session.user

	if APPROVER_ROLE not in set(frappe.get_roles(user)):
		return _("You need the {0} role to decide agent proposals.").format(APPROVER_ROLE)

	if action.requested_by and action.requested_by == user:
		return _("You requested this proposal. Someone else has to decide it.")

	run_user = frappe.db.get_value("Agent Run", action.run, "effective_user") if action.run else None
	if run_user and run_user == user:
		return _("This proposal came out of a run acting as you. Someone else has to decide it.")

	agent_owner = frappe.db.get_value("Agent", action.agent, "owner") if action.agent else None
	if agent_owner and agent_owner == user:
		return _("You own the agent {0}. Someone else has to decide what it proposes.").format(action.agent)

	return None


def is_stale(action: Any) -> bool:
	"""True when a Pending proposal is already past the expiry window."""
	created = get_datetime(action.creation)
	if not created:
		return False
	return created < _cutoff(_expiry_days())


def notify_approvers(action_name: str) -> None:
	"""Tell the approvers who could actually decide a new proposal that it is waiting.

	A background job, queued after the proposal commits. It never raises: a
	notification that fails is an Error Log, and the proposal stands either way.
	"""
	with _never_raises("frappe_agents: approver notification failed"):
		action = frappe.db.get_value("Agent Action", action_name, list(NOTICE_FIELDS), as_dict=True)
		if not action or action.status != STATUS_PENDING:
			return
		if not frappe.db.exists(action.target_doctype, action.target_name):
			return
		target = frappe.get_doc(action.target_doctype, action.target_name)

		recipients = eligible_approvers(action, target)
		if not recipients:
			return
		make_notification_logs(
			frappe._dict(
				{
					"type": NOTIFICATION_TYPE,
					"document_type": "Agent Action",
					"document_name": action.name,
					"from_user": action.requested_by,
					"subject": _approver_subject(action),
				}
			),
			recipients,
		)


def eligible_approvers(action: Any, target: Any) -> list[str]:
	"""The approvers who may decide `action` and could carry it through.

	The same walls `approve_action` puts up, asked ahead of time: the
	separation-of-duties block, read on the target, and the right the apply will
	need. For a submit or a cancel that right is submit or cancel. For a workflow
	action it is a role the step is open to, plus the right its target state needs.
	An approver who fails any of them would only be told about a proposal they
	cannot open or cannot apply. The step's condition and self-approval stay the
	Workflow's call, made when the approver applies it.
	"""
	steps = _approval_steps(action, target)
	eligible = []
	for user in sorted(get_users_with_role(APPROVER_ROLE)):
		if separation_of_duties_block(action, user):
			continue
		if not frappe.has_permission(action.target_doctype, "read", doc=target, user=user):
			continue
		if not _may_take_a_step(action, target, user, steps):
			continue
		eligible.append(user)
		if len(eligible) >= MAX_NOTIFIED_APPROVERS:
			break
	return eligible


def _approval_steps(action: Any, target: Any) -> list[tuple[str | None, str]]:
	"""The ways an approver could apply `action`: (role required, right required).

	A submit or a cancel has one way, open to any role. A workflow action has one
	per matching transition in the Workflow, and none at all when the Workflow
	changed or the document left the state the proposal was made from — the apply
	would fail on those, so nobody is asked to try it.
	"""
	if action.action_type == ACTION_SUBMIT:
		return [(None, "submit")]
	if action.action_type == ACTION_CANCEL:
		return [(None, "cancel")]
	if action.action_type != ACTION_TRANSITION:
		return []

	if not action.workflow_name or get_workflow_name(action.target_doctype) != action.workflow_name:
		return []
	workflow = get_workflow(action.target_doctype)
	if target.get(workflow.workflow_state_field) != action.from_workflow_state:
		return []

	docstatus = {row.state: cint(row.doc_status) for row in workflow.states}
	before = docstatus.get(action.from_workflow_state, DRAFT)
	steps = []
	for row in workflow.transitions:
		if row.state != action.from_workflow_state or row.action != action.workflow_action:
			continue
		steps.append((row.allowed, _right_for(before, docstatus.get(row.next_state, before))))
	return steps


def _right_for(before: int, after: int) -> str:
	"""The document right a workflow step needs, from the docstatus it moves between."""
	if after == SUBMITTED and before == DRAFT:
		return "submit"
	if after == CANCELLED:
		return "cancel"
	if before == SUBMITTED:
		# A step between two submitted states updates a submitted document.
		return "submit"
	return "write"


def _may_take_a_step(action: Any, target: Any, user: str, steps: list[tuple[str | None, str]]) -> bool:
	roles = set(frappe.get_roles(user)) if any(role for role, _right in steps) else set()
	for role, right in steps:
		if role and role not in roles:
			continue
		if frappe.has_permission(action.target_doctype, right, doc=target, user=user):
			return True
	return False


def _enqueue_approver_notice(action_name: str) -> None:
	# After commit, so a proposal that rolls back tells nobody. Inline under test,
	# where nothing ever commits.
	try:
		frappe.enqueue(
			"frappe_agents.actions.notify_approvers",
			queue="short",
			enqueue_after_commit=True,
			now=bool(frappe.flags.in_test),
			action_name=action_name,
		)
	except Exception:
		frappe.log_error(title="frappe_agents: approver notification failed")


def _notify_requester(action: Any, status: str, note: str | None = None) -> None:
	"""Tell whoever asked for a proposal how it ended. Never raises.

	The requester and the run's effective user, when that is someone else. The
	decider is left out: they know. Called after the status write, so a failure
	here is logged and the decision stands.
	"""
	with _never_raises("frappe_agents: requester notification failed"):
		decider = None if status == STATUS_EXPIRED else frappe.session.user
		recipients = _requester_recipients(action, decider)
		if not recipients:
			return

		notice = {
			"type": NOTIFICATION_TYPE,
			"document_type": "Agent Action",
			"document_name": action.name,
			"subject": _requester_subject(action, status, decider, note),
		}
		if decider:
			notice["from_user"] = decider
		enqueue_create_notification(recipients, notice)

		for user in recipients:
			frappe.publish_realtime(
				"agent_action_decided",
				{"action": action.name, "status": status, "run": action.run},
				user=user,
				after_commit=True,
			)


def _requester_recipients(action: Any, decider: str | None) -> list[str]:
	run_user = frappe.db.get_value("Agent Run", action.run, "effective_user") if action.run else None
	recipients = []
	for user in (action.requested_by, run_user):
		if user and user != decider and user not in recipients:
			recipients.append(user)
	return recipients


def _approver_subject(action: Any) -> str:
	# Agent, verb and target identity only. The reason is model-written and the
	# fields are the target's business: both stay on the Agent Action form, behind
	# its own permission check, and out of the bell.
	if action.action_type == ACTION_TRANSITION:
		template = _("{0} proposes the workflow action {3} on {1} {2}. It needs an approver.")
	elif action.action_type == ACTION_CANCEL:
		template = _("{0} proposes to cancel {1} {2}. It needs an approver.")
	else:
		template = _("{0} proposes to submit {1} {2}. It needs an approver.")
	return template.format(
		escape_html(action.agent or _("An agent")),
		escape_html(_(action.target_doctype)),
		escape_html(action.target_name),
		_workflow_step(action),
	)


def _workflow_step(action: Any) -> str:
	"""The workflow verb with the state it starts from. The same verb can be another
	step out of another state, so the verb alone does not say which step this is."""
	if not action.workflow_action:
		return ""
	verb = escape_html(_(action.workflow_action))
	if not action.from_workflow_state:
		return verb
	return _("{0} (from {1})").format(verb, escape_html(_(action.from_workflow_state)))


def _requester_subject(action: Any, status: str, decider: str | None, note: str | None = None) -> str:
	# The subject is the whole notice: released frappe shows nothing else in the
	# bell, and an Alert is never emailed. So a rejection's note rides in it, cut
	# short, and the full note stays on the Agent Action form.
	templates = _requester_templates(action.action_type)
	subject = templates[status].format(
		escape_html(get_fullname(decider) if decider else ""),
		escape_html(_(action.target_doctype)),
		escape_html(action.target_name),
		_workflow_step(action),
	)
	if note:
		subject = _("{0} Note: {1}").format(subject, escape_html(_shorten(note, SUBJECT_NOTE_LIMIT)))
	return subject


def _requester_templates(action_type: str) -> dict:
	# Whole sentences per verb, so a translator never has to glue fragments.
	if action_type == ACTION_TRANSITION:
		return {
			STATUS_APPLIED: _("{0} approved the workflow action {3} on {1} {2}."),
			STATUS_REJECTED: _("{0} rejected the workflow action {3} on {1} {2}."),
			STATUS_FAILED: _("The workflow action {3} on {1} {2} failed when {0} approved it."),
			STATUS_EXPIRED: _("The proposed workflow action {3} on {1} {2} expired with no decision."),
		}
	if action_type == ACTION_CANCEL:
		return {
			STATUS_APPLIED: _("{0} approved the proposal to cancel {1} {2}."),
			STATUS_REJECTED: _("{0} rejected the proposal to cancel {1} {2}."),
			STATUS_FAILED: _("The proposal to cancel {1} {2} failed when {0} approved it."),
			STATUS_EXPIRED: _("The proposal to cancel {1} {2} expired with no decision."),
		}
	return {
		STATUS_APPLIED: _("{0} approved the proposal to submit {1} {2}."),
		STATUS_REJECTED: _("{0} rejected the proposal to submit {1} {2}."),
		STATUS_FAILED: _("The proposal to submit {1} {2} failed when {0} approved it."),
		STATUS_EXPIRED: _("The proposal to submit {1} {2} expired with no decision."),
	}


def _shorten(text: str, limit: int) -> str:
	text = " ".join(strip_html(text).split())
	return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@contextmanager
def _never_raises(title: str):
	"""Run a notification step; on any failure, undo its writes and log it.

	The savepoint matters under test, where the notification inserts inline: a
	half-written batch is rolled back rather than committed with the decision.
	"""
	frappe.db.savepoint(NOTIFY_SAVEPOINT)
	try:
		yield
	except Exception:
		frappe.db.rollback(save_point=NOTIFY_SAVEPOINT)
		frappe.log_error(title=title)


def state_fields() -> list[str]:
	"""The Agent Action columns a chat card and the agent's history are built from."""
	return list(STATE_FIELDS)


def proposal_state(row: Any) -> dict:
	"""One proposal as the session user may see it: where it stands, and why.

	Everything about the target is taken out when the session user cannot read
	the target today: its doctype and name, the agent's reason, the approver's
	note and the failure text. The note and the failure are about the document,
	and a failure is the document's own validation message, which may quote its
	fields. What stays is what is about the proposal itself: its status, the verb,
	the run, and when it was decided.
	"""
	row = frappe._dict(row)
	state = {
		"action": row.name,
		"run": row.run,
		"status": row.status,
		"action_type": row.action_type,
		"workflow_action": row.get("workflow_action"),
		"workflow_name": row.get("workflow_name"),
		"from_workflow_state": row.get("from_workflow_state"),
		"target_doctype": row.target_doctype,
		"target_name": row.target_name,
		"reason": row.reason,
		"decision_note": row.decision_note,
		"failure": row.failure,
		"decided_at": row.decided_at,
		"target_visible": 1,
		"can_decide": 0,
	}
	if row.status == STATUS_PENDING and is_stale(row):
		# Past its window and refused by `approve_action` already; the daily sweep
		# writes it down later. Until then it is shown as what it is.
		state["status"] = STATUS_EXPIRED

	# A target that is gone has nobody to ask about, so it is not shown at all:
	# the doctype-level right says nothing about one document's User Permissions.
	exists = bool(
		row.target_doctype and row.target_name and frappe.db.exists(row.target_doctype, row.target_name)
	)
	if not exists or not target_readable(row):
		state.update(
			{
				"target_visible": 0,
				"target_doctype": None,
				"target_name": None,
				# A workflow's names are part of the target's process, and can say
				# what kind of document it is.
				"workflow_action": None,
				"workflow_name": None,
				"from_workflow_state": None,
				"reason": None,
				"decision_note": None,
				"failure": None,
			}
		)
		return state

	if state["status"] == STATUS_PENDING:
		state["can_decide"] = 0 if separation_of_duties_block(row) else 1
	return state


def rejected_proposal_for(
	target_doctype: str,
	target_name: str,
	action_type: str,
	modified: Any,
	workflow_action: str | None = None,
	from_workflow_state: str | None = None,
) -> Any:
	"""This user's latest rejected proposal for the same action on this document, if any.

	The row carries `same_version`: whether it was rejected on the document as it
	is now. Read through `get_list`, so it finds the session user's own proposals
	and nobody else's. A workflow action is the same action only with the same verb
	out of the same state.
	"""
	filters = {
		"target_doctype": target_doctype,
		"target_name": target_name,
		"action_type": action_type,
		"status": STATUS_REJECTED,
	}
	if workflow_action:
		filters["workflow_action"] = workflow_action
	if from_workflow_state:
		filters["from_workflow_state"] = from_workflow_state
	rows = frappe.get_list(
		"Agent Action",
		filters=filters,
		fields=["name", "decision_note", "proposal_modified"],
		order_by="creation desc",
		limit_page_length=REJECTION_LOOKBACK,
	)
	if not rows:
		return None
	for row in rows:
		if _same_timestamp(row.proposal_modified, modified):
			row.same_version = True
			return row
	rows[0].same_version = False
	return rows[0]


def target_readable(row: Any) -> bool:
	"""Whether the session user may read the document a proposal is about."""
	doctype, name = row.get("target_doctype"), row.get("target_name")
	if not doctype or not name:
		return False
	if not frappe.db.exists(doctype, name):
		# The target is gone, so there is no document to check against. The
		# doctype-level right is the most that can be asked here, and the approval
		# will fail on the missing document anyway.
		return bool(frappe.has_permission(doctype, "read"))
	return bool(frappe.has_permission(doctype, "read", doc=name))


def _check_kill_switch() -> None:
	"""The kill switch stops the apply too.

	global_enabled = 0 means the site distrusts the runtime. A human applying agent
	output during that state is still acting on agent output.

	`runtime_enabled` is the one reader in the app, and it is the one reader for a
	reason: a cached copy of Agent Settings is per process, so the same switch could
	be off for a run and still on for the approval of what that run proposed.
	"""
	if not runtime_enabled():
		frappe.throw(_("The agent runtime is switched off. Agent proposals cannot be applied."))


def _load_action(action: str) -> Any:
	if not action or not isinstance(action, str):
		frappe.throw(_("Which proposal? An Agent Action name is required."))
	frappe.has_permission("Agent Action", "read", doc=action, throw=True)
	return frappe.get_doc("Agent Action", action)


def _require_pending(action: Any) -> None:
	if action.status != STATUS_PENDING:
		frappe.throw(
			_("This proposal is already {0}. Only a pending proposal can be decided.").format(
				_(action.status)
			)
		)


def _check_expiry(action: Any) -> None:
	"""Refuse a proposal that is past its window but not yet swept.

	It refuses without writing Expired: this raises, and a raise rolls the request
	back, so the write would be lost anyway. The daily sweep is what moves the row.
	"""
	if not is_stale(action):
		return
	frappe.throw(
		_("This proposal expired after {0} days without a decision. The agent can propose it again.").format(
			_expiry_days()
		)
	)


def _require_separation_of_duties(action: Any) -> None:
	block = separation_of_duties_block(action)
	if block:
		raise frappe.PermissionError(block)


def _load_target(action: Any) -> Any:
	"""The document the proposal is about, once this approver is allowed to see it.

	Fail closed, and before anything is written. An approval is a person saying
	they read the record and agree; an approval of a document they may not open is
	a signature on a blank page. `get_doc` checks nothing on its own, and the apply
	only ever exercises submit or cancel — read is the one right this path would
	otherwise never prove.
	"""
	if not frappe.db.exists(action.target_doctype, action.target_name):
		return None
	frappe.has_permission(action.target_doctype, "read", doc=action.target_name, throw=True)
	return frappe.get_doc(action.target_doctype, action.target_name)


def _check_lock(action: Any, target: Any, expected_modified: Any) -> None:
	"""Refuse an approval of a version the approver did not look at."""
	expected = _as_instant(expected_modified)
	if not expected:
		# Fail closed. An unreadable timestamp is not a match, it is a missing lock.
		frappe.throw(
			_("expected_modified is required: send the modified timestamp of the document you reviewed.")
		)

	if not _same_timestamp(target.modified, expected):
		# Raised by hand. Frappe's own check compares the timestamp the document was
		# loaded with, which on this path is always current, so it can never fire here.
		frappe.throw(
			_(
				"{0} {1} changed since you looked at it. Reload the proposal, read what "
				"changed, and decide again."
			).format(action.target_doctype, action.target_name),
			frappe.TimestampMismatchError,
		)


def _apply_problem(action: Any, target: Any) -> str | None:
	"""Why this proposal cannot be applied to the document as it is now, if it cannot."""
	workflow = get_workflow_name(action.target_doctype)

	if action.action_type == ACTION_TRANSITION:
		if not workflow:
			return _(
				"{0} is no longer governed by an active Workflow, so there is no transition to apply."
			).format(action.target_doctype)
		return _transition_problem(action, target, workflow)

	# Re-checked here, not only at proposal time: a Workflow activated in between
	# would otherwise be bypassed by this submit, silently and without a trace.
	if workflow:
		return _(
			"{0} is now governed by an active Workflow ({1}). Agent proposals cannot "
			"submit or cancel workflow-governed documents; the agent can propose a workflow action instead."
		).format(action.target_doctype, workflow)

	return _state_problem(action, target)


def _transition_problem(action: Any, target: Any, workflow: str) -> str | None:
	"""Why this workflow action may not run on the document as it is now, if it may not.

	A workflow action is a verb, and verbs repeat: Draft -Approve-> Manager Approved
	-Approve-> Approved is an ordinary two-level approval. `apply_workflow` resolves
	the verb against the state the document is in *now*, so a proposal made from the
	first state, approved after someone took that step by hand, would take the second
	step instead — one the run's user may never have been offered. The proposal is
	bound to the Workflow and the state it was made from, and anything else fails.
	The timestamp lock cannot stand in for this: an approver who reloads sends the
	new timestamp and passes it.
	"""
	if not action.workflow_action:
		return _("This proposal names no workflow action.")
	if not action.workflow_name or not action.from_workflow_state:
		# Fail closed: without the state it was made from, the verb has no meaning.
		return _("This proposal does not record the workflow state it was made from. Ask the agent again.")
	if workflow != action.workflow_name:
		return _(
			"{0} is now governed by the Workflow {1}, not {2} as when the agent proposed. Ask the agent again."
		).format(action.target_doctype, workflow, action.workflow_name)

	state = target.get(get_workflow(action.target_doctype).workflow_state_field)
	if state != action.from_workflow_state:
		return _(
			"{0} {1} has moved from {2} to {3} since the agent proposed {4}. The proposal was "
			"for the step out of {2}, so it cannot be applied now."
		).format(
			action.target_doctype,
			action.target_name,
			action.from_workflow_state,
			state,
			action.workflow_action,
		)

	if _queues_in_background(action.target_doctype):
		return _(
			"{0} submits and cancels in the background, so this approval could not tell whether the "
			"workflow action went through. Apply it from the document itself."
		).format(action.target_doctype)

	# Whether the step is open to this approver, and its condition, are the
	# Workflow's call, made inside apply_workflow.
	return None


def _queues_in_background(doctype: str) -> bool:
	"""Whether Frappe would hand this doctype's submit or cancel to a background job.

	`apply_workflow` then returns before anything is written, and the row would say
	Applied about a document that has not moved.
	"""
	return bool(cint(frappe.get_meta(doctype).queue_in_background))


def _state_problem(action: Any, target: Any) -> str | None:
	docstatus = cint(target.docstatus)
	if action.action_type == ACTION_SUBMIT and docstatus != DRAFT:
		return _("{0} {1} is no longer a draft, so this proposal cannot be applied.").format(
			action.target_doctype, action.target_name
		)
	if action.action_type == ACTION_CANCEL and docstatus != SUBMITTED:
		return _("{0} {1} is not submitted, so there is nothing to cancel.").format(
			action.target_doctype, action.target_name
		)
	return None


def _record_failure(action: Any, note: str | None, edited: int, failure: str) -> dict:
	_write(
		action,
		{
			"status": STATUS_FAILED,
			"decided_by": frappe.session.user,
			"decided_at": now_datetime(),
			"decision_note": note,
			"edited_before_approval": edited,
			"failure": failure[:FAILURE_LIMIT],
		},
	)
	_notify_requester(action, STATUS_FAILED)
	return {
		"action": action.name,
		"status": STATUS_FAILED,
		"target_doctype": action.target_doctype,
		"target_name": action.target_name,
		"failure": failure[:FAILURE_LIMIT],
	}


def _write(action: Any, values: dict) -> None:
	# Agent Action grants write to no role: every status transition goes through
	# here, and carries the decider and the moment in the same statement.
	action.flags.ignore_permissions = True
	action.db_set(values)


def _failure_text(exc: Exception) -> str:
	"""The sentence to put on the row — including the one PermissionError hides.

	`frappe.PermissionError` is raised bare, so `str(exc)` is empty; the human-readable
	text is left in `frappe.flags.error_message` and in the message log.
	"""
	text = strip_html(str(exc) or "").strip()
	if not text:
		text = strip_html(str(frappe.flags.get("error_message") or "")).strip()
	if not text:
		text = " ".join(_message_log()).strip()
	return (text or exc.__class__.__name__)[:FAILURE_LIMIT]


def _message_log() -> list[str]:
	messages = []
	for entry in frappe.get_message_log() or []:
		raw = entry.get("message") if isinstance(entry, dict) else entry
		text = strip_html(str(raw or "")).strip()
		if text:
			messages.append(text)
	return messages


def _same_timestamp(left: Any, right: Any) -> bool:
	"""Compare two `modified` values as instants, never as strings."""
	left, right = get_datetime(left), get_datetime(right)
	return bool(left) and bool(right) and left == right


def _as_instant(value: Any) -> Any:
	"""`value` as a datetime, or None if it is not one.

	`get_datetime` is only total over strings and datetimes: it reads None as *now*
	and treats a list as constructor arguments. Since this is what the lock is
	checked against, anything outside those two types is a missing lock, not a
	timestamp — it comes back None and the caller refuses.
	"""
	if not isinstance(value, str | datetime.datetime):
		return None
	return get_datetime(value)


def _cutoff(days: int) -> Any:
	"""The moment `days` ago, as a datetime.

	Fed a datetime, never a string: `add_to_date` flips its own return type to a
	string whenever the date it is handed is one, and a datetime will not compare
	against that.
	"""
	return add_to_date(now_datetime(), days=-days)


def _expiry_days() -> int:
	days = cint(frappe.db.get_single_value("Agent Settings", "action_expiry_days"))
	return days if days > 0 else DEFAULT_EXPIRY_DAYS


def _clean_note(note: Any) -> str | None:
	if not isinstance(note, str):
		return None
	note = note.strip()
	if not note:
		return None
	if len(note) > NOTE_LIMIT:
		frappe.throw(_("The note is too long: {0} characters, limit is {1}.").format(len(note), NOTE_LIMIT))
	return note
