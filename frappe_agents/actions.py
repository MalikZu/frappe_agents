"""The approval engine: what happens to an agent's proposal once a human sees it.

An Agent Action is a request, not an act. `approve_action` is where it becomes an
act, and it does so **as the approver** — `doc.submit()` and `doc.cancel()` run in
the approver's own session under the approver's own permissions. There is no
`ignore_permissions` on the apply path, deliberately: an approver who may not
submit the document may not cause it to be submitted either.

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
from frappe.model.workflow import get_workflow_name
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

DRAFT = 0
SUBMITTED = 1

DEFAULT_EXPIRY_DAYS = 7
FAILURE_LIMIT = 500
NOTE_LIMIT = 1000
SAVEPOINT = "agent_action_apply"

# A proposal is a question for a few people, not a broadcast. Past this many
# eligible approvers the rest are not told; they still see it in the queue.
MAX_NOTIFIED_APPROVERS = 20
NOTIFICATION_TYPE = "Alert"
NOTIFY_SAVEPOINT = "agent_action_notify"
# What a notification is built from. Never the reason, never the target's fields.
NOTICE_FIELDS = (
	"name",
	"status",
	"run",
	"agent",
	"requested_by",
	"action_type",
	"target_doctype",
	"target_name",
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

	# Re-checked here, not only at proposal time: a Workflow activated in between
	# would otherwise be bypassed by this submit, silently and without a trace.
	workflow = get_workflow_name(doc_action.target_doctype)
	if workflow:
		return _record_failure(
			doc_action,
			note,
			0,
			_(
				"{0} is now governed by an active Workflow ({1}). Agent proposals cannot "
				"submit or cancel workflow-governed documents; use the workflow's own transitions."
			).format(doc_action.target_doctype, workflow),
		)

	state_problem = _state_problem(doc_action, target)
	if state_problem:
		return _record_failure(doc_action, note, 0, state_problem)

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
		if doc_action.action_type == ACTION_SUBMIT:
			target.submit()
		else:
			target.cancel()
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
		"docstatus": cint(target.docstatus),
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


def pending_action_for(target_doctype: str, target_name: str, action_type: str) -> str | None:
	"""The Pending proposal already covering this target and action, if there is one."""
	return frappe.db.exists(
		"Agent Action",
		{
			"target_doctype": target_doctype,
			"target_name": target_name,
			"action_type": action_type,
			"status": STATUS_PENDING,
		},
	)


def record_proposal(
	run: Any,
	action_type: str,
	target_doctype: str,
	target_name: str,
	reason: str,
	proposal_modified: Any,
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
	for row in stale:
		_notify_requester(row, STATUS_EXPIRED)
	return len(stale)


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

	The same three walls `approve_action` puts up, asked ahead of time: the
	separation-of-duties block, read on the target, and the submit or cancel right
	the apply will need. An approver who fails any of them would only be told
	about a proposal they cannot open or cannot apply.
	"""
	right = "submit" if action.action_type == ACTION_SUBMIT else "cancel"
	eligible = []
	for user in sorted(get_users_with_role(APPROVER_ROLE)):
		if separation_of_duties_block(action, user):
			continue
		if not frappe.has_permission(action.target_doctype, "read", doc=target, user=user):
			continue
		if not frappe.has_permission(action.target_doctype, right, doc=target, user=user):
			continue
		eligible.append(user)
		if len(eligible) >= MAX_NOTIFIED_APPROVERS:
			break
	return eligible


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
			"subject": _requester_subject(action, status, decider),
		}
		if decider:
			notice["from_user"] = decider
		if note:
			notice["email_content"] = escape_html(note)
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
	# its own permission check, and out of the notification and its email.
	template = (
		_("{0} proposes to cancel {1} {2}. It needs an approver.")
		if action.action_type == ACTION_CANCEL
		else _("{0} proposes to submit {1} {2}. It needs an approver.")
	)
	return template.format(
		escape_html(action.agent or _("An agent")),
		escape_html(_(action.target_doctype)),
		escape_html(action.target_name),
	)


def _requester_subject(action: Any, status: str, decider: str | None) -> str:
	cancel = action.action_type == ACTION_CANCEL
	templates = {
		STATUS_APPLIED: (
			_("{0} approved the proposal to cancel {1} {2}.")
			if cancel
			else _("{0} approved the proposal to submit {1} {2}.")
		),
		STATUS_REJECTED: (
			_("{0} rejected the proposal to cancel {1} {2}.")
			if cancel
			else _("{0} rejected the proposal to submit {1} {2}.")
		),
		STATUS_FAILED: (
			_("The proposal to cancel {1} {2} failed when {0} approved it.")
			if cancel
			else _("The proposal to submit {1} {2} failed when {0} approved it.")
		),
		STATUS_EXPIRED: (
			_("The proposal to cancel {1} {2} expired with no decision.")
			if cancel
			else _("The proposal to submit {1} {2} expired with no decision.")
		),
	}
	return templates[status].format(
		escape_html(get_fullname(decider) if decider else ""),
		escape_html(_(action.target_doctype)),
		escape_html(action.target_name),
	)


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
