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

from typing import Any

import frappe
from frappe import _
from frappe.model.workflow import get_workflow_name
from frappe.utils import add_to_date, cint, get_datetime, now_datetime, strip_html

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


@frappe.whitelist(methods=["POST"])
def approve_action(action: str, expected_modified: str, note: str | None = None) -> dict:
	"""Approve one Agent Action and apply it as the approver.

	`expected_modified` is the target document's `modified` as it was on the screen
	the approver decided from. It is required, and a mismatch refuses the approval.
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
	return action


def expire_stale_actions() -> int:
	"""Expire proposals nobody decided. Daily job, and safe to run by hand.

	Idempotent by construction: it selects only Pending rows, so a second run in the
	same window matches nothing. It writes no decider because nobody decided, and it
	leaves `modified` alone because expiry is the clock, not an edit.
	"""
	days = _expiry_days()
	filters = {"status": STATUS_PENDING, "creation": ("<", _cutoff(days))}

	stale = frappe.get_all("Agent Action", filters=filters, pluck="name")
	if not stale:
		return 0

	frappe.db.set_value(
		"Agent Action",
		filters,
		{
			"status": STATUS_EXPIRED,
			"failure": _("Expired after {0} days with no decision.").format(days),
		},
		update_modified=False,
	)
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


def _check_kill_switch() -> None:
	"""The kill switch stops the apply too.

	global_enabled = 0 means the site distrusts the runtime. A human applying agent
	output during that state is still acting on agent output.
	"""
	settings = frappe.get_cached_doc("Agent Settings")
	if not cint(settings.global_enabled):
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
	if not frappe.db.exists(action.target_doctype, action.target_name):
		return None
	return frappe.get_doc(action.target_doctype, action.target_name)


def _check_lock(action: Any, target: Any, expected_modified: Any) -> None:
	"""Refuse an approval of a version the approver did not look at."""
	expected = get_datetime(expected_modified)
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
