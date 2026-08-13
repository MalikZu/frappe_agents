"""Whitelisted endpoints for the Desk chat surface and the review surface.

Nothing here runs as anyone but the session user. An Agent Run is queued, never
executed inline, and the run carries the identity it must be executed under.

`apply_extraction` is the one place in the app that may write an extracted
sensitive value into a document, and only for a fieldname the caller named in
`confirmed`. No confidence score, no clean master match and no status shortcut
reaches past that list.
"""

import re
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime, today

from frappe_agents.actions import APPROVER_ROLE, separation_of_duties_block
from frappe_agents.extraction.pipeline import (
	EXTRACTION,
	STATUS_ACCEPTED,
	STATUS_DISCARDED,
	STATUS_NEEDS_REVIEW,
	publish_extraction,
	queue_extraction,
	record_fields,
)
from frappe_agents.extraction.schema import build_extraction_schema
from frappe_agents.tools.draft_tools import SYSTEM_FIELDS

MAX_MESSAGE_CHARS = 20_000
TITLE_CHARS = 140
DESCRIPTION_CHARS = 200

DEFAULT_PENDING_LIMIT = 50
MAX_PENDING_LIMIT = 200


@frappe.whitelist()
def list_agents() -> list[dict]:
	"""Enabled agents the session user is allowed to talk to."""
	roles = set(frappe.get_roles())
	agents = frappe.get_list(
		"Agent",
		filters={"enabled": 1},
		fields=["name", "agent_name"],
		order_by="agent_name asc",
		limit_page_length=0,
	)

	allowed = []
	for row in agents:
		agent = frappe.get_cached_doc("Agent", row.name)
		if not _user_may_use(agent, roles):
			continue
		allowed.append(
			{
				"name": agent.name,
				"agent_name": agent.agent_name,
				"autonomy": agent.autonomy,
				"description": _description(agent),
			}
		)
	return allowed


@frappe.whitelist(methods=["POST"])
def start_run(
	agent: str,
	message: str,
	conversation: str | None = None,
	context_doctype: str | None = None,
	context_name: str | None = None,
) -> dict:
	"""Queue one turn of a conversation with an agent. Returns the run and conversation."""
	message = (message or "").strip()
	if not message:
		frappe.throw(_("Message cannot be empty."))
	if len(message) > MAX_MESSAGE_CHARS:
		frappe.throw(
			_("Message is too long: {0} characters, limit is {1}.").format(len(message), MAX_MESSAGE_CHARS)
		)

	settings = frappe.get_cached_doc("Agent Settings")
	if not cint(settings.global_enabled):
		frappe.throw(_("The agent runtime is switched off."))

	frappe.has_permission("Agent", "read", doc=agent, throw=True)
	agent_doc = frappe.get_doc("Agent", agent)

	if not cint(agent_doc.enabled):
		frappe.throw(_("Agent {0} is disabled.").format(agent_doc.name))

	if not _user_may_use(agent_doc, set(frappe.get_roles())):
		raise frappe.PermissionError(_("You are not allowed to use the agent {0}.").format(agent_doc.name))

	if agent_doc.run_as != "Session User":
		frappe.throw(
			_("Agent {0} runs as a service user and cannot be started from chat.").format(agent_doc.name)
		)

	context_doctype, context_name = _validate_context(context_doctype, context_name)

	_check_budget(agent_doc, settings)

	conversation_doc = _get_conversation(agent_doc, conversation, message)

	run = frappe.get_doc(
		{
			"doctype": "Agent Run",
			"agent": agent_doc.name,
			"conversation": conversation_doc.name,
			"effective_user": frappe.session.user,
			"run_as_mode": "Session User",
			"surface": "Desk Chat",
			"status": "Queued",
			"depth": 0,
			"input_message": message,
			"context_doctype": context_doctype,
			"context_name": context_name,
		}
	)
	run.flags.ignore_permissions = True
	run.insert(ignore_permissions=True)

	conversation_doc.flags.ignore_permissions = True
	conversation_doc.db_set("last_activity", now_datetime(), update_modified=False)

	frappe.enqueue(
		"frappe_agents.runner.run.execute_run",
		queue="long",
		enqueue_after_commit=True,
		run_name=run.name,
	)

	return {"run": run.name, "conversation": conversation_doc.name}


@frappe.whitelist()
def get_conversation(conversation: str) -> dict:
	"""A conversation and its runs, as the session user is permitted to see them.

	Each run carries its `event_log` parsed into a list of events, oldest first.
	That is what the chat surface redraws a proposal card from after a reload —
	the card is published while the run is going, and a reload has nothing else
	to learn it from. No extra permission is involved: the log belongs to a run
	of this conversation, which the caller has just been checked for.
	"""
	frappe.has_permission("Agent Conversation", "read", doc=conversation, throw=True)
	doc = frappe.get_doc("Agent Conversation", conversation)

	runs = frappe.get_list(
		"Agent Run",
		filters={"conversation": doc.name},
		fields=["name", "status", "input_message", "output_message", "error", "creation", "event_log"],
		order_by="creation asc",
		limit_page_length=0,
	)
	for run in runs:
		run["event_log"] = _run_events(run.get("event_log"))

	return {
		"name": doc.name,
		"agent": doc.agent,
		"title": doc.title,
		"last_activity": doc.last_activity,
		"runs": runs,
	}


def _run_events(log: Any) -> list[dict]:
	"""The events a run recorded, as a list.

	The column is JSON written by the runner. A log that is missing, empty or
	unreadable replays as nothing — a conversation must still open when one of
	its runs wrote a log nobody can parse.
	"""
	if not log:
		return []
	try:
		parsed = frappe.parse_json(log)
	except Exception:
		return []
	events = parsed.get("events") if isinstance(parsed, dict) else None
	if not isinstance(events, list):
		return []
	return [event for event in events if isinstance(event, dict)]


@frappe.whitelist()
def list_pending_actions(limit: int | None = None) -> list[dict]:
	"""The proposals waiting on this approver, oldest first.

	Reads through `frappe.get_list`, so an approver only sees the rows their
	permissions already allow. Each row carries whether *this* user may decide it —
	the separation of duties is a property of the pair, not of the row, and the
	queue should say so before the approver opens anything.
	"""
	if APPROVER_ROLE not in set(frappe.get_roles()):
		raise frappe.PermissionError(
			_("You need the {0} role to review agent proposals.").format(APPROVER_ROLE)
		)

	rows = frappe.get_list(
		"Agent Action",
		filters={"status": "Pending"},
		fields=[
			"name",
			"action_type",
			"target_doctype",
			"target_name",
			"reason",
			"agent",
			"run",
			"requested_by",
			"proposal_modified",
			"creation",
		],
		order_by="creation asc",
		limit_page_length=_pending_limit(limit),
	)

	for row in rows:
		block = separation_of_duties_block(frappe._dict(row))
		row["can_decide"] = 0 if block else 1
		row["blocked_because"] = block
		# What the approver will be asked to send back as expected_modified, and the
		# first hint that the document moved since the agent proposed it.
		row["target_modified"] = _target_modified(row)
		row["edited_since_proposal"] = (
			0 if _same_instant(row["target_modified"], row.get("proposal_modified")) else 1
		)

	return rows


def _pending_limit(limit: Any) -> int:
	limit = cint(limit) or DEFAULT_PENDING_LIMIT
	return max(1, min(limit, MAX_PENDING_LIMIT))


def _target_modified(row: dict) -> Any:
	doctype, name = row.get("target_doctype"), row.get("target_name")
	if not doctype or not name:
		return None
	if not frappe.has_permission(doctype, "read", doc=name):
		return None
	return frappe.db.get_value(doctype, name, "modified")


def _same_instant(left: Any, right: Any) -> bool:
	left, right = get_datetime(left), get_datetime(right)
	return bool(left) and bool(right) and left == right


def _validate_context(doctype: Any, name: Any) -> tuple[str | None, str | None]:
	"""A run may only be opened on a document the user can already read.

	The check happens here, before anything is written: a queued run seeded with a
	document its user cannot read would hand that document to the agent under the
	user's own identity.
	"""
	doctype = (doctype or "").strip() if isinstance(doctype, str) else ""
	name = str(name).strip() if name not in (None, "") else ""

	if not doctype and not name:
		return None, None
	if not doctype or not name:
		frappe.throw(_("A run on a document needs both context_doctype and context_name."))

	if not frappe.db.exists("DocType", doctype):
		frappe.throw(_("No such DocType: {0}").format(doctype))
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("No such document: {0} {1}").format(doctype, name))

	frappe.has_permission(doctype, "read", doc=name, throw=True)
	return doctype, name


def _user_may_use(agent: Any, roles: set) -> bool:
	# Agent User is the baseline: it carries the doctype permissions that make
	# conversations and runs readable. allowed_roles only narrows WHICH agents.
	if "Agent User" not in roles:
		return False
	allowed_roles = {row.role for row in (agent.get("allowed_roles") or []) if row.role}
	return not allowed_roles or bool(allowed_roles & roles)


def _description(agent: Any) -> str:
	instructions = (agent.instructions or "").strip()
	if not instructions:
		return ""
	first_line = instructions.splitlines()[0].strip()
	return first_line[:DESCRIPTION_CHARS]


def _check_budget(agent: Any, settings: Any) -> None:
	budget = cint(agent.daily_token_budget) or cint(settings.default_daily_token_budget)
	if not budget:
		return

	# Budget is per agent per day across all its users, so this counts every run.
	rows = frappe.get_all(
		"Agent Run",
		filters={"agent": agent.name, "creation": (">=", today())},
		fields=["tokens_in", "tokens_out"],
		limit_page_length=0,
	)
	used = sum(cint(row.tokens_in) + cint(row.tokens_out) for row in rows)
	if used >= budget:
		frappe.throw(
			_("Agent {0} has used its daily token budget ({1} of {2}). Try again tomorrow.").format(
				agent.name, used, budget
			)
		)


def _get_conversation(agent: Any, conversation: str | None, message: str) -> Any:
	if conversation:
		frappe.has_permission("Agent Conversation", "write", doc=conversation, throw=True)
		doc = frappe.get_doc("Agent Conversation", conversation)
		if doc.user != frappe.session.user:
			raise frappe.PermissionError(_("This conversation belongs to another user."))
		if doc.agent != agent.name:
			frappe.throw(_("This conversation belongs to agent {0}.").format(doc.agent))
		return doc

	doc = frappe.get_doc(
		{
			"doctype": "Agent Conversation",
			"agent": agent.name,
			"user": frappe.session.user,
			"title": message[:TITLE_CHARS],
			"last_activity": now_datetime(),
		}
	)
	doc.insert()
	return doc


# --- Document extraction ----------------------------------------------------
#
# Three endpoints and one rule between them: `start_extraction` and the pipeline
# behind it never write a sensitive field, and `apply_extraction` writes one only
# when the caller names it. The review surface is the Document Extraction form, so
# these are called from form JS with the reviewer's own session.

SYSTEM_MANAGER = "System Manager"
SENSITIVE_ROW = re.compile(r"^(?P<table>[a-z_][a-z0-9_]*)\[(?P<index>\d+)\]\.(?P<field>[a-z_][a-z0-9_]*)$")


@frappe.whitelist(methods=["POST"])
def start_extraction(file: str, target_doctype: str, model_profile: str | None = None) -> dict:
	"""Queue an extraction of one file into one doctype, as the session user."""
	name = queue_extraction(file, target_doctype, model_profile=model_profile)
	return {"extraction": name, "status": "Pending"}


@frappe.whitelist(methods=["POST"])
def apply_extraction(name: str, values: Any = None, confirmed: Any = None) -> dict:
	"""Write the reviewed values onto the draft and accept the extraction.

	`values` is what the reviewer has in front of them, edits included. `confirmed`
	is the list of sensitive fieldnames they explicitly confirmed, and it is the
	only way a sensitive value is ever written: a sensitive field that appears in
	`values` but not in `confirmed` is dropped and named in the answer.
	"""
	doc = _extraction_for_review(name)
	if doc.status != STATUS_NEEDS_REVIEW:
		frappe.throw(
			_("This extraction is {0}. Only one waiting for review can be accepted.").format(doc.status)
		)
	if not doc.created_doc:
		frappe.throw(_("This extraction has no draft to apply to."))

	values = _as_dict(values)
	confirmed = _as_list(confirmed)

	spec = build_extraction_schema(doc.target_doctype, frappe.session.user)
	flags = frappe.parse_json(doc.sensitive_flags) or {}

	reviewed, ignored = _reviewed_values(spec, values, flags)
	applied, withheld = _confirmed_values(flags, values, confirmed)

	draft = frappe.get_doc(doc.target_doctype, doc.created_doc)
	if cint(draft.docstatus) != 0:
		frappe.throw(_("{0} {1} is no longer a draft.").format(doc.target_doctype, doc.created_doc))
	if not frappe.has_permission(doc.target_doctype, "write", doc=draft):
		raise frappe.PermissionError(
			_("You are not allowed to change {0} {1}.").format(doc.target_doctype, doc.created_doc)
		)

	draft.update(reviewed)
	unwritable = _write_confirmed(draft, applied)
	# No ignore_permissions: the reviewer's own write permission carries the save,
	# and this time the document is validated in full — the holes left for review
	# have to be filled before it is accepted.
	draft.save()

	stamped = _stamp(flags, applied)
	record_fields(
		doc,
		{
			"status": STATUS_ACCEPTED,
			"reviewed_by": frappe.session.user,
			"extracted_json": frappe.as_json(reviewed),
			"sensitive_flags": frappe.as_json(stamped),
		},
	)
	publish_extraction(doc, status=STATUS_ACCEPTED, created_doc=doc.created_doc)

	return {
		"extraction": doc.name,
		"status": STATUS_ACCEPTED,
		"created_doc": doc.created_doc,
		"applied_fields": sorted(reviewed),
		"confirmed_fields": sorted(applied),
		"withheld_fields": sorted(withheld),
		"unwritable_fields": sorted(unwritable),
		"ignored_keys": sorted(ignored),
	}


@frappe.whitelist(methods=["POST"])
def discard_extraction(name: str) -> dict:
	"""Close an extraction without applying it. Terminal."""
	doc = _extraction_for_review(name)
	if doc.status in (STATUS_ACCEPTED, STATUS_DISCARDED):
		frappe.throw(_("This extraction is already {0}.").format(doc.status))

	record_fields(doc, {"status": STATUS_DISCARDED, "reviewed_by": frappe.session.user})
	publish_extraction(doc, status=STATUS_DISCARDED)

	return {
		"extraction": doc.name,
		"status": STATUS_DISCARDED,
		"created_doc": doc.created_doc,
		"note": (
			_(
				"Any draft this extraction created is left alone — deleting a document is a "
				"decision for a person, not a side effect of closing a review."
			)
			if doc.created_doc
			else None
		),
	}


def _extraction_for_review(name: str) -> Any:
	"""The extraction, if this user owns it or manages the system."""
	doc = frappe.get_doc(EXTRACTION, name)
	if doc.owner != frappe.session.user and SYSTEM_MANAGER not in set(frappe.get_roles()):
		raise frappe.PermissionError(_("This extraction belongs to {0}.").format(doc.owner))
	return doc


def _reviewed_values(spec: dict, values: dict, flags: dict) -> tuple[dict, list[str]]:
	"""Everything the reviewer sent that is a field we asked the model for.

	Sensitive fields are dropped here without exception — they come back through
	`_confirmed_values` or not at all.
	"""
	fields = spec.get("fields") or {}
	tables = spec.get("child_tables") or {}
	sensitive = set(spec.get("sensitive") or []) | set(flags or {})

	clean: dict[str, Any] = {}
	ignored: list[str] = []

	for key, value in values.items():
		if key in sensitive:
			# Handled by the confirmation path, or not at all. Never here.
			continue
		if key in SYSTEM_FIELDS or str(key).startswith("__"):
			ignored.append(key)
			continue
		if key in fields:
			clean[key] = value
			continue
		if key in tables and isinstance(value, list):
			clean[key] = [_reviewed_row(tables[key], row) for row in value if isinstance(row, dict)]
			continue
		ignored.append(key)

	return clean, ignored


def _reviewed_row(table: dict, row: dict) -> dict:
	fields = table.get("fields") or {}
	sensitive = set(table.get("sensitive") or [])
	return {
		key: value
		for key, value in row.items()
		if key in fields and key not in sensitive and key not in SYSTEM_FIELDS
	}


def _confirmed_values(flags: dict, values: dict, confirmed: list[str]) -> tuple[dict, list[str]]:
	"""The sensitive values the reviewer confirmed, and the ones they did not.

	A confirmation names a field. The value written is the one the reviewer has in
	front of them — their edit if they made one, otherwise what was extracted —
	because confirming means "I have checked this against something outside this
	document", and that is a statement about a value, not about a fieldname.
	"""
	applied: dict[str, Any] = {}
	for key in confirmed:
		if key not in flags:
			frappe.throw(_("{0} is not a sensitive field on this extraction.").format(key))
		applied[key] = values.get(key, (flags.get(key) or {}).get("value"))

	withheld = [key for key in flags if key not in applied]
	return applied, withheld


def _write_confirmed(draft: Any, applied: dict) -> list[str]:
	"""Set each confirmed value on the draft, child rows included."""
	unwritable = []
	for key, value in applied.items():
		match = SENSITIVE_ROW.match(key)
		if not match:
			draft.set(key, value)
			continue

		rows = draft.get(match.group("table")) or []
		index = int(match.group("index"))
		if index >= len(rows):
			# The reviewer replaced the rows and the one this value belonged to is
			# gone. Say so rather than writing it into whichever row is there now.
			unwritable.append(key)
			continue
		rows[index].set(match.group("field"), value)

	return unwritable


def _stamp(flags: dict, applied: dict) -> dict:
	"""Record on each flag what was confirmed, by whom, and with which value."""
	stamped = dict(flags)
	stamp = str(now_datetime())
	for key, flag in stamped.items():
		if not isinstance(flag, dict):
			continue
		if key in applied:
			flag["confirmed"] = 1
			flag["confirmed_by"] = frappe.session.user
			flag["confirmed_at"] = stamp
			flag["applied_value"] = applied[key]
		else:
			flag["confirmed"] = 0
	return stamped


def _as_dict(value: Any) -> dict:
	value = frappe.parse_json(value) if isinstance(value, str) else value
	return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[str]:
	value = frappe.parse_json(value) if isinstance(value, str) else value
	if not isinstance(value, list):
		return []
	return [str(item) for item in value if isinstance(item, str | int)]
