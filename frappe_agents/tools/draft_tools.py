"""Draft writes, and proposals for everything past the draft.

An agent writes drafts and proposes the rest. A draft commits the business to
nothing and can be corrected or deleted by any human who can see it, so the agent
owns that space outright: `create_draft` and `update_draft` write straight through
the effective user's own permissions, with no `ignore_permissions` anywhere.

Crossing the docstatus boundary is different. A submitted document is a commitment
and a cancelled one is a reversal, so `propose_submit` and `propose_cancel` write
no document at all — they insert one Agent Action row saying what the agent wants
and why, and a human decides it in `frappe_agents.actions`.

Two refusals in here carry more weight than they look:

* **Workflow-governed doctypes.** Frappe does not block a server-side `doc.submit()`
  on a doctype with an active Workflow — it silently jumps the document to the first
  submitted state, skipping every approval in between. The refusal below is the only
  thing that stops an agent proposal from becoming that bypass, and `approve_action`
  repeats it before it applies anything.
* **System fields.** Values from the model may never set identity, ownership,
  timestamps, docstatus, or a child row's parent linkage. They are stripped and the
  stripped names are handed back, so the model learns the rule rather than silently
  losing the write.
"""

from typing import Any

import frappe
from frappe.model import table_fields
from frappe.model.workflow import get_workflow_name
from frappe.utils import cint, get_datetime, strip_html

from frappe_agents.runner.run import publish_event
from frappe_agents.tools.base import CAPABILITY_DRAFT, ToolDenied, current_run

ACTION_SUBMIT = "Submit"
ACTION_CANCEL = "Cancel"
STATUS_PENDING = "Pending"

REASON_LIMIT = 2000
MESSAGE_LIMIT = 10

DRAFT = 0
SUBMITTED = 1
CANCELLED = 2

# Never settable from a tool payload. Identity, ownership, the audit timestamps and
# the docstatus itself: everything that decides what a document *is*, as opposed to
# what it says. `docstatus` is the one that matters most — the whole point of this
# module is that an agent cannot cross that boundary by writing a field.
SYSTEM_FIELDS = (
	"doctype",
	"name",
	"__newname",
	"naming_series",
	"docstatus",
	"idx",
	"owner",
	"creation",
	"modified",
	"modified_by",
	"parent",
	"parenttype",
	"parentfield",
	"_user_tags",
	"_comments",
	"_assign",
	"_liked_by",
)


def create_draft(payload: dict) -> dict:
	"""Insert one document at docstatus 0 as the effective user."""
	doctype = _require_str(payload, "doctype")
	values = _require_dict(payload, "values")
	dry_run = _as_bool(payload.get("dry_run"))

	meta = _writable_meta(doctype)
	if not frappe.has_permission(doctype, "create"):
		raise ToolDenied(f"You are not allowed to create {doctype}.")

	clean, stripped = _strip_system_fields(meta, values)
	doc = frappe.get_doc(dict(clean, doctype=doctype))

	if dry_run:
		return dict(_dry_run(doc), doctype=doctype, stripped_keys=stripped)

	frappe.clear_messages()
	# No ignore_permissions: insert runs the create check itself, as the user.
	doc.insert()

	return {
		"doctype": doctype,
		"name": doc.name,
		"status_word": "DRAFT",
		"stripped_keys": stripped,
		"messages": _drain_messages(),
		"_docs_touched": f"{doctype}: {doc.name}",
	}


def update_draft(payload: dict) -> dict:
	"""Update a document that is still a draft, as the effective user."""
	doctype = _require_str(payload, "doctype")
	name = _require_str(payload, "name")
	values = _require_dict(payload, "values")

	meta = _writable_meta(doctype)
	doc = _load(doctype, name)

	if not frappe.has_permission(doctype, "write", doc=doc):
		raise ToolDenied(f"You are not allowed to change {doctype} {name}.")

	if cint(doc.docstatus) != DRAFT:
		raise ToolDenied(
			f"{doctype} {name} is {_status_word(doc.docstatus).lower()}, not a draft. "
			"Agents only edit drafts. To undo a submitted document, propose a cancellation "
			"with propose_cancel and say why."
		)

	clean, stripped = _strip_system_fields(meta, values)
	frappe.clear_messages()
	doc.update(clean)
	# No ignore_permissions: save runs the write check itself, as the user.
	doc.save()

	return {
		"doctype": doctype,
		"name": doc.name,
		"status_word": "DRAFT",
		"updated_fields": sorted(clean),
		"stripped_keys": stripped,
		"messages": _drain_messages(),
		"_docs_touched": f"{doctype}: {doc.name}",
	}


def propose_submit(payload: dict) -> dict:
	"""Ask a human to submit a draft. Writes no document, only the proposal."""
	return _propose(payload, ACTION_SUBMIT)


def propose_cancel(payload: dict) -> dict:
	"""Ask a human to cancel a submitted document. Writes no document, only the proposal."""
	return _propose(payload, ACTION_CANCEL)


def _propose(payload: dict, action_type: str) -> dict:
	doctype = _require_str(payload, "doctype")
	name = _require_str(payload, "name")
	reason = _require_reason(payload)
	run = _require_run(action_type)

	doc = _load(doctype, name)
	if not frappe.has_permission(doctype, "read", doc=doc):
		raise ToolDenied(f"You are not allowed to read {doctype} {name}.")

	meta = frappe.get_meta(doctype)
	if not cint(meta.is_submittable):
		raise ToolDenied(
			f"{doctype} is not a submittable doctype — it has no submit or cancel step to propose."
		)

	_check_target_state(doc, action_type)
	_refuse_workflow(doctype)
	_refuse_duplicate(doctype, name, action_type)

	action = frappe.get_doc(
		{
			"doctype": "Agent Action",
			"run": run.name,
			"agent": run.agent,
			"requested_by": frappe.session.user,
			"action_type": action_type,
			"target_doctype": doctype,
			"target_name": name,
			"reason": reason,
			# The lock and the review metric both hang off this one snapshot, so it is
			# stored as a datetime — never a string, whose formatting is not stable.
			"proposal_modified": get_datetime(doc.modified),
			"status": STATUS_PENDING,
		}
	)
	# Agent Action grants create to nobody: rows move through this path and the
	# whitelisted decision methods, never through a form.
	action.flags.ignore_permissions = True
	action.insert(ignore_permissions=True)

	publish_event(
		run,
		"action_proposed",
		action=action.name,
		action_type=action_type,
		target_doctype=doctype,
		target_name=name,
		reason=reason,
	)

	return {
		"action": action.name,
		"action_type": action_type,
		"status": STATUS_PENDING,
		"target_doctype": doctype,
		"target_name": name,
		"note": (
			f"Proposed. {doctype} {name} is unchanged and stays "
			f"{_status_word(doc.docstatus).lower()} until an approver decides."
		),
		"_docs_touched": f"{doctype}: {name}, Agent Action: {action.name}",
	}


def _check_target_state(doc: Any, action_type: str) -> None:
	docstatus = cint(doc.docstatus)
	if action_type == ACTION_SUBMIT and docstatus != DRAFT:
		raise ToolDenied(
			f"{doc.doctype} {doc.name} is already {_status_word(docstatus).lower()}. "
			"Only a draft can be proposed for submission."
		)
	if action_type == ACTION_CANCEL and docstatus != SUBMITTED:
		raise ToolDenied(
			f"{doc.doctype} {doc.name} is {_status_word(docstatus).lower()}. "
			"Only a submitted document can be proposed for cancellation."
		)


def _refuse_workflow(doctype: str) -> None:
	"""Refuse a doctype whose transitions belong to a Workflow.

	`get_workflow_name` returns the workflow's name, an empty string on a cached
	miss, or None on the first miss — so this tests truthiness, never `is None`.
	"""
	workflow = get_workflow_name(doctype)
	if workflow:
		raise ToolDenied(
			f"{doctype} is governed by an active Workflow ({workflow}). Agent proposals "
			"cannot submit or cancel workflow-governed documents; use the workflow's own "
			"transitions."
		)


def _refuse_duplicate(doctype: str, name: str, action_type: str) -> None:
	existing = frappe.db.exists(
		"Agent Action",
		{
			"target_doctype": doctype,
			"target_name": name,
			"action_type": action_type,
			"status": STATUS_PENDING,
		},
	)
	if existing:
		raise ToolDenied(
			f"There is already a pending {action_type.lower()} proposal for {doctype} {name} "
			f"({existing}). Wait for that decision instead of proposing again."
		)


def _dry_run(doc: Any) -> dict:
	"""Run the validations an insert would run, and write nothing.

	The point is to learn whether the document *would* be accepted, so a validation
	failure is an answer rather than an error: it comes back as text the model can act on.
	"""
	frappe.clear_messages()
	doc._action = "save"
	# What a real save would do first. Without it, validate_set_only_once reaches for
	# a _doc_before_save that only the save path sets and dies with an AttributeError.
	doc.load_doc_before_save()
	try:
		doc.run_method("before_validate")
		doc.run_method("validate")
		doc._validate()
	except frappe.ValidationError as exc:
		return {
			"dry_run": True,
			"would_insert": False,
			"validation_error": _text(exc),
			"messages": _drain_messages(),
		}
	return {"dry_run": True, "would_insert": True, "messages": _drain_messages()}


def _strip_system_fields(meta: Any, values: dict) -> tuple[dict, list[str]]:
	"""Drop what a tool payload may never set, and say what was dropped.

	Child tables are replaced wholesale, not merged: a list in `values` becomes the
	table. Each row is stripped the same way, and a row may never carry its own
	parent linkage.
	"""
	clean: dict = {}
	stripped: list[str] = []

	for key, value in values.items():
		if key in SYSTEM_FIELDS or key.startswith("__"):
			stripped.append(key)
			continue

		df = meta.get_field(key)
		if not df:
			raise ValueError(f"Unknown field {key} on {meta.name}.")

		if df.fieldtype in table_fields:
			rows, row_stripped = _strip_rows(df, key, value)
			clean[key] = rows
			stripped.extend(row_stripped)
			continue

		clean[key] = value

	return clean, stripped


def _strip_rows(df: Any, fieldname: str, value: Any) -> tuple[list, list[str]]:
	if value is None:
		return [], []
	if not isinstance(value, list):
		raise ValueError(f"{fieldname} is a table: pass a list of rows.")

	child_meta = frappe.get_meta(df.options)
	rows: list[dict] = []
	stripped: list[str] = []

	for index, row in enumerate(value):
		if not isinstance(row, dict):
			raise ValueError(f"{fieldname} row {index + 1} must be an object of fieldname to value.")
		clean_row: dict = {}
		for key, cell in row.items():
			if key in SYSTEM_FIELDS or key.startswith("__"):
				stripped.append(f"{fieldname}[{index}].{key}")
				continue
			if not child_meta.get_field(key):
				raise ValueError(f"Unknown field {key} on {df.options}.")
			clean_row[key] = cell
		rows.append(clean_row)

	return rows, stripped


def _writable_meta(doctype: str) -> Any:
	if not frappe.db.exists("DocType", doctype):
		raise ValueError(f"No such DocType: {doctype}")

	meta = frappe.get_meta(doctype)
	if cint(meta.istable):
		raise ToolDenied(f"{doctype} is a child table. Write its rows through the parent document instead.")
	if cint(meta.issingle):
		raise ToolDenied(f"{doctype} is a single settings document, not something to draft.")
	return meta


def _load(doctype: str, name: str) -> Any:
	if not frappe.db.exists("DocType", doctype):
		raise ValueError(f"No such DocType: {doctype}")
	try:
		return frappe.get_doc(doctype, name)
	except frappe.DoesNotExistError:
		raise ValueError(f"No such document: {doctype} {name}")


def _require_run(action_type: str) -> Any:
	run = current_run()
	if run is None or not run.get("name"):
		raise ToolDenied(
			f"propose_{action_type.lower()} records who asked and why, so it only works inside an agent run."
		)
	return run


def _require_reason(payload: dict) -> str:
	reason = payload.get("reason")
	if not isinstance(reason, str) or not reason.strip():
		raise ValueError("reason is required: say why this should happen. A human reads it before deciding.")
	reason = reason.strip()
	if len(reason) > REASON_LIMIT:
		raise ValueError(f"reason is too long: {len(reason)} characters, limit is {REASON_LIMIT}.")
	return reason


def _require_str(payload: dict, key: str) -> str:
	value = payload.get(key)
	if not isinstance(value, str) or not value.strip():
		raise ValueError(f"{key} is required and must be a string.")
	return value.strip()


def _require_dict(payload: dict, key: str) -> dict:
	value = payload.get(key)
	if not isinstance(value, dict) or not value:
		raise ValueError(f"{key} is required and must be a non-empty object of fieldname to value.")
	return value


def _as_bool(value: Any) -> bool:
	if isinstance(value, str):
		return value.strip().lower() in ("1", "true", "yes")
	return bool(value)


def _status_word(docstatus: Any) -> str:
	return {DRAFT: "DRAFT", SUBMITTED: "SUBMITTED", CANCELLED: "CANCELLED"}.get(cint(docstatus), "DRAFT")


def _drain_messages() -> list[str]:
	"""Whatever the controller said out loud during the write, as plain text."""
	messages = []
	for entry in frappe.get_message_log() or []:
		text = entry.get("message") if isinstance(entry, dict) else entry
		text = strip_html(str(text or "")).strip()
		if text:
			messages.append(text)
	frappe.clear_messages()
	return messages[:MESSAGE_LIMIT]


def _text(exc: Exception) -> str:
	return strip_html(str(exc) or "").strip() or exc.__class__.__name__


TOOLS = [
	{
		"tool_name": "create_draft",
		"handler_path": "frappe_agents.tools.draft_tools.create_draft",
		"capability": CAPABILITY_DRAFT,
		"description": (
			"Create one document as a draft (docstatus 0) with the current user's own "
			"create permission. A draft commits nothing and can be corrected. Values that "
			"set identity, ownership, timestamps or docstatus are dropped and listed back "
			"under stripped_keys — a draft cannot be made submitted by writing a field. "
			"Child tables take a list of rows and replace the whole table. Pass dry_run to "
			"run the validations without writing anything."
		),
		"args_schema": {
			"type": "object",
			"properties": {
				"doctype": {"type": "string", "description": "DocType to create, e.g. 'Task'."},
				"values": {
					"type": "object",
					"description": (
						'Fieldname to value, e.g. {"subject": "Fix the pump", "items": '
						'[{"item": "Seal", "qty": 2}]}.'
					),
				},
				"dry_run": {
					"type": "boolean",
					"description": "Validate only. Nothing is written. Default false.",
				},
			},
			"required": ["doctype", "values"],
		},
	},
	{
		"tool_name": "update_draft",
		"handler_path": "frappe_agents.tools.draft_tools.update_draft",
		"capability": CAPABILITY_DRAFT,
		"description": (
			"Change a document that is still a draft, with the current user's own write "
			"permission. Submitted and cancelled documents are refused: those are decided "
			"by a human through propose_cancel. Child tables replace the whole table, so "
			"send every row you want to keep. Stripped system fields come back under "
			"stripped_keys."
		),
		"args_schema": {
			"type": "object",
			"properties": {
				"doctype": {"type": "string", "description": "DocType of the draft."},
				"name": {"type": "string", "description": "Name of the draft document."},
				"values": {"type": "object", "description": "Fieldname to new value."},
			},
			"required": ["doctype", "name", "values"],
		},
	},
	{
		"tool_name": "propose_submit",
		"handler_path": "frappe_agents.tools.draft_tools.propose_submit",
		"capability": CAPABILITY_DRAFT,
		"description": (
			"Propose that a human submit a draft. This does not submit anything: it records "
			"a pending Agent Action with your reason, and an approver decides. Say in the "
			"reason what the document commits to and what you checked — that text is what "
			"the approver reads. Doctypes governed by a Workflow are refused."
		),
		"args_schema": {
			"type": "object",
			"properties": {
				"doctype": {"type": "string", "description": "DocType of the draft."},
				"name": {"type": "string", "description": "Name of the draft document."},
				"reason": {
					"type": "string",
					"description": "Why this should be submitted, in your own words. Required.",
				},
			},
			"required": ["doctype", "name", "reason"],
		},
	},
	{
		"tool_name": "propose_cancel",
		"handler_path": "frappe_agents.tools.draft_tools.propose_cancel",
		"capability": CAPABILITY_DRAFT,
		"description": (
			"Propose that a human cancel a submitted document. This does not cancel "
			"anything: it records a pending Agent Action with your reason, and an approver "
			"decides. Cancelling undoes something the business already committed to, so say "
			"what makes that the right call. Doctypes governed by a Workflow are refused."
		),
		"args_schema": {
			"type": "object",
			"properties": {
				"doctype": {"type": "string", "description": "DocType of the submitted document."},
				"name": {"type": "string", "description": "Name of the submitted document."},
				"reason": {
					"type": "string",
					"description": "Why this should be cancelled, in your own words. Required.",
				},
			},
			"required": ["doctype", "name", "reason"],
		},
	},
]
