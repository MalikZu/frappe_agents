# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Applying an approved field change to a live record, and showing it before that.

`frappe_agents.actions` owns the decision; this module owns what an Update
decision checks and does. Everything here runs as the approver, in their own
session, and nothing here passes `ignore_permissions`.

Four checks look redundant and are not:

* **Write at each field's permlevel, asked before the save.** Frappe's `save()`
  does not refuse a field the user may not write at a higher permlevel: it
  quietly puts the old value back and saves the rest. An approval would be
  recorded as Applied with nothing changed. Administrator skips that reset
  altogether, so a test run as Administrator would never see it.
* **Every saved value read back.** A controller may rewrite a field during
  `validate`. The approver approved a value, not "whatever the save produced",
  so a value that did not survive the save rolls the whole change back.
* **The Workflow's edit role.** A Workflow says which role may edit a document
  in each state, and frappe enforces that only in the form, not on a server-side
  save. Without this check an approval would edit through a state lock.
* **Each field against the value the agent saw.** The `modified` lock says the
  approver looked at this version of the document. This says the agent's
  premise still holds: a proposal to raise a priority from Low is not a proposal
  to raise it from High. An edit to a field the proposal does not touch passes,
  and is recorded as an edit before approval.
"""

from typing import Any

import frappe
from frappe import _
from frappe.model.workflow import get_workflow, get_workflow_name
from frappe.utils import cint

from frappe_agents.field_changes import (
	UnusableValue,
	field_refusal,
	has_field_access,
	is_blank,
	live_problem,
	loads,
	normalise,
	precision_of,
	same_value,
	sensitive_fields,
)

ACTION_UPDATE = "Update"
STATUS_PENDING = "Pending"
FIELD_SEPARATOR = ", "


def stored_change(action: Any) -> tuple[dict, dict]:
	"""The proposed values and the values the agent saw, as dicts."""
	return loads(action.get("proposed_values")), loads(action.get("seen_values"))


def update_problem(action: Any, target: Any) -> str | None:
	"""Why this Update cannot be applied by the session user now, if it cannot.

	The wording never repeats a field's value: the text lands on the Agent Action
	row and in the requester's notification, and both are read by people who may
	not read the field.
	"""
	meta = frappe.get_meta(action.target_doctype)
	problem = live_problem(meta)
	if problem:
		return problem
	if cint(target.docstatus) != 0:
		return _("{0} {1} is no longer an open record, so its fields cannot be changed this way.").format(
			action.target_doctype, action.target_name
		)

	proposed, seen = stored_change(action)
	if not proposed:
		return _("This proposal names no field to change.")

	if not frappe.has_permission(action.target_doctype, "write", doc=target):
		return _("You are not allowed to change {0} {1}, so approving cannot change it either.").format(
			action.target_doctype, action.target_name
		)

	problem = workflow_edit_problem(target)
	if problem:
		return problem

	for fieldname, value in proposed.items():
		problem = _field_problem(meta, target, fieldname, value, seen)
		if problem:
			return problem
	return None


def _field_problem(meta: Any, target: Any, fieldname: str, value: Any, seen: dict) -> str | None:
	refusal = field_refusal(meta, fieldname, target)
	if refusal:
		return _("This change can no longer be applied. {0}").format(refusal)

	df = meta.get_field(fieldname)
	label = _(df.label or fieldname)
	if not has_field_access(target, df, "write"):
		return _("You are not allowed to change {0} on {1}, so approving cannot change it either.").format(
			label, action_target(target)
		)

	if fieldname not in seen:
		return _("This proposal does not record what {0} was when the agent proposed it.").format(label)
	if not same_value(df, target.get(fieldname), seen[fieldname], precision_of(target, df)):
		return _(
			"{0} changed after the agent proposed this. The proposal was about a value that no "
			"longer holds, so it was not applied. The agent can propose again."
		).format(label)

	if df.fieldtype == "Link" and not is_blank(value):
		if not frappe.db.exists(df.options, value):
			return _("The {0} the proposal sets {1} to no longer exists.").format(_(df.options), label)
		if not frappe.has_permission(df.options, "read", doc=value):
			return _("The proposal sets {0} to a {1} you are not allowed to read.").format(
				label, _(df.options)
			)
	return None


def workflow_edit_problem(target: Any, user: str | None = None) -> str | None:
	"""Why `user` — the session user by default — may not edit `target` in its workflow state."""
	if not get_workflow_name(target.doctype):
		return None
	workflow = get_workflow(target.doctype)
	state = target.get(workflow.workflow_state_field) or (
		workflow.states[0].state if workflow.states else None
	)
	row = next((row for row in workflow.states if row.state == state), None)
	if row is None:
		return _("{0} is in a state its Workflow does not list, so it cannot be changed this way.").format(
			action_target(target)
		)
	if row.allow_edit and row.allow_edit not in frappe.get_roles(user):
		return _("In the workflow state {0}, only the {1} role may edit {2}.").format(
			_(state), _(row.allow_edit), action_target(target)
		)
	return None


def apply_update(action: Any, target: Any) -> None:
	"""Write the approved values and prove they stayed. Raises on any shortfall.

	The caller holds the savepoint: a raise here rolls the document back whole.
	"""
	meta = frappe.get_meta(action.target_doctype)
	proposed, _seen = stored_change(action)

	target.update(proposed)
	# No ignore_permissions: save runs the write check itself, as the approver.
	target.save()

	saved = frappe.get_doc(action.target_doctype, action.target_name)
	for fieldname, value in proposed.items():
		df = meta.get_field(fieldname)
		if not same_value(df, saved.get(fieldname), value, precision_of(saved, df)):
			frappe.throw(
				_(
					"{0} did not keep the approved value: the document's own rules changed it while "
					"saving. Nothing was changed."
				).format(_(df.label or fieldname))
			)


def update_diff(action: Any) -> dict:
	"""The before, now and proposed value of each field, for the session user.

	A field the session user may not read at its permlevel, or that is masked
	for them, comes back with its label only. A document they may not read comes
	back with no fields at all.
	"""
	result = {"fields": [], "target_readable": 0, "changed_since": 0}
	if not frappe.db.exists(action.target_doctype, action.target_name):
		return result
	if not frappe.has_permission(action.target_doctype, "read", doc=action.target_name):
		return result

	target = frappe.get_doc(action.target_doctype, action.target_name)
	meta = frappe.get_meta(action.target_doctype)
	proposed, seen = stored_change(action)
	hidden = sensitive_fields(meta)
	order = {df.fieldname: index for index, df in enumerate(meta.fields)}

	result["target_readable"] = 1
	for fieldname in sorted(proposed, key=lambda name: order.get(name, len(order))):
		df = meta.get_field(fieldname)
		row = {
			"fieldname": fieldname,
			"label": _(df.label or fieldname) if df else fieldname,
			"fieldtype": df.fieldtype if df else None,
			"options": df.options if df and df.fieldtype in ("Link", "Select") else None,
			"readable": 0,
		}
		readable = df is not None and fieldname not in hidden and has_field_access(target, df, "read")
		if readable:
			precision = precision_of(target, df)
			now = _shown(df, target.get(fieldname), precision)
			moved = not same_value(df, target.get(fieldname), seen.get(fieldname), precision)
			row.update(
				readable=1,
				before=seen.get(fieldname),
				now=now,
				proposed=proposed[fieldname],
				changed_since=1 if moved else 0,
			)
			if moved:
				result["changed_since"] = 1
		result["fields"].append(row)
	return result


def pending_update_fields(doctype: str, name: str) -> dict[str, set[str]]:
	"""Each pending Update on one document, and the fields it would change.

	Reads the app's own Agent Action rows, never the target's data — the same kind
	of bookkeeping lookup as `pending_action_for`. Check-then-insert with no lock:
	two proposals racing on one field can both land, and the per-field stale check
	fails whichever is approved second.
	"""
	rows = frappe.get_all(
		"Agent Action",
		filters={
			"target_doctype": doctype,
			"target_name": name,
			"action_type": ACTION_UPDATE,
			"status": STATUS_PENDING,
		},
		fields=["name", "update_fields"],
		limit_page_length=0,
	)
	return {row.name: field_names(row.update_fields) for row in rows}


def field_names(stored: Any) -> set[str]:
	if not isinstance(stored, str):
		return set()
	return {part.strip() for part in stored.split(",") if part.strip()}


def action_target(target: Any) -> str:
	return f"{_(target.doctype)} {target.name}"


def _shown(df: Any, value: Any, precision: int | None) -> Any:
	try:
		return normalise(df, value, precision)
	except UnusableValue:
		return None if value is None else str(value)
