# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""What the Agents Builder needs to see: the shape of the site, never its data.

Designing an agent means naming real doctypes and real reports. The matrix is
explicit by design — there is no wildcard rule — so an agent that had to be
granted every doctype before it could suggest one would be the widest agent on
the site. These three tools are the way out: they answer with **names, modules,
descriptions and fieldnames** and never with a single document.

That is why they sit apart from `get_doctype_meta`, which stays matrix-scoped
and answers only for granted targets. Reading a doctype's fields is not reading
a record, and the two questions get two tools.

Three limits hold here, and together they are the whole gate:

* **The exclusions apply.** A doctype the matrix may never name is not listed,
  not described, and not confirmed to exist by these tools either. One thing is
  described and still never named: the blueprint's own child tables. A blueprint
  cannot be written without their fieldnames, and describing a table that no rule
  may target and no tool may read out of grants nothing — see `is_describable`.
* **The session user's own permissions apply.** The list is the doctypes *they*
  may read — an agent never sees more of the site than the person it runs for.
* **Permlevel 0 only.** Fields above the base level are named under
  `restricted_fields` and never described, because a blueprint has no business
  proposing access to them.

Reaching the tools at all takes the grant to draft an Agent Blueprint. That
check lives in `access/grants.py` with every other one, and is asked for here
through `tools/base` like all the rest.
"""

from typing import Any

import frappe
from frappe.model import no_value_fields
from frappe.permissions import get_doctypes_with_read
from frappe.utils import cint

from frappe_agents.access.exclusions import (
	BLUEPRINT,
	describable_child_tables,
	is_describable,
	is_excluded,
)
from frappe_agents.tools.base import (
	CAPABILITY_READ,
	ToolDenied,
	require_blueprint_drafting,
)

MAX_LIMIT = 100
DEFAULT_LIMIT = 50

# One answer to three questions: the doctype does not exist, no rule may ever
# name it, or this user may not read it. Told apart, they are a way to map the
# site by asking — the refusal that says "no such thing" confirms every other
# name is a thing. So all three refuse in the same words, with the same
# exception, and the difference stays inside the tool.
NO_SUCH_DOCTYPE = "There is no DocType named {doctype} for you to describe."

REPORT_FIELDS = ("name", "report_name", "report_type", "ref_doctype", "module", "disabled")


def list_site_doctypes(payload: dict) -> dict:
	"""Name, module and description of the doctypes the current user may read."""
	require_blueprint_drafting()

	query = _query(payload)
	module = _optional_str(payload, "module").lower()
	start, limit = _page(payload)

	rows = []
	for doctype in sorted(get_doctypes_with_read()):
		if is_excluded(doctype):
			continue
		meta = frappe.get_meta(doctype)
		if cint(meta.istable):
			# A child table is written through its parent and granted through it too.
			continue
		description = meta.description or ""
		if module and module not in (meta.module or "").lower():
			continue
		if query and query not in doctype.lower() and query not in description.lower():
			continue
		rows.append(
			{
				"doctype": doctype,
				"module": meta.module,
				"description": description,
				"is_submittable": cint(meta.is_submittable),
				"is_single": cint(meta.issingle),
			}
		)

	return _page_result(rows, "doctypes", start, limit)


def list_site_reports(payload: dict) -> dict:
	"""Saved reports the current user may read, for the rules that grant one.

	`total` counts the reports the user may read and this app may grant. The page
	is then held to the reports they may actually *run*: a Report can name the
	roles allowed to run it, and that check needs the record, so it is made on the
	page rather than on the whole site.
	"""
	require_blueprint_drafting()

	query = _query(payload)
	start, limit = _page(payload)

	if not frappe.has_permission("Report", "read"):
		raise ToolDenied("You are not allowed to read reports.")

	filters: dict = {"disabled": 0}
	if query:
		filters["name"] = ("like", f"%{query}%")

	candidates = frappe.get_list(
		"Report",
		filters=filters,
		fields=list(REPORT_FIELDS),
		order_by="name asc",
		limit_page_length=0,
	)

	rows = [row for row in candidates if not (row.get("ref_doctype") and is_excluded(row["ref_doctype"]))]
	page = [row for row in rows[start : start + limit] if _may_run(row["name"])]

	return {
		"reports": [
			{
				"report": row["name"],
				"report_type": row.get("report_type"),
				"doctype": row.get("ref_doctype"),
				"module": row.get("module"),
			}
			for row in page
		],
		"count": len(page),
		"total": len(rows),
		"start": start,
		"truncated": start + limit < len(rows),
		"_result_summary": f"{len(page)} of {len(rows)} report(s)",
	}


def describe_site_doctype(payload: dict) -> dict:
	"""The fields of one doctype, so a blueprint can name the right records.

	Fields only. Nothing here reads a document, and nothing here is a grant: the
	agent being described stays as reachable, or as unreachable, as it was.
	"""
	require_blueprint_drafting()

	doctype = _require_str(payload, "doctype")
	# Order is cheapest first and says nothing: whichever of the three it was, the
	# refusal is the same one. `db.exists` runs before the permission check because
	# `has_permission` on a name that is not a doctype is not a question.
	if (
		not is_describable(doctype)
		or not frappe.db.exists("DocType", doctype)
		or not frappe.has_permission(_permission_target(doctype), "read")
	):
		raise ToolDenied(NO_SUCH_DOCTYPE.format(doctype=doctype))

	meta = frappe.get_meta(doctype)

	fields = []
	restricted = []
	for df in meta.fields:
		if df.fieldtype in no_value_fields:
			continue
		if cint(df.permlevel):
			# Above the base level is somebody's deliberate wall. A blueprint that
			# proposed reaching over it would be proposing something the matrix
			# does not even express.
			restricted.append(df.fieldname)
			continue
		fields.append(
			{
				"fieldname": df.fieldname,
				"label": df.label,
				"fieldtype": df.fieldtype,
				"options": df.options,
				"reqd": cint(df.reqd),
			}
		)

	return {
		"doctype": doctype,
		"module": meta.module,
		"description": meta.description or "",
		"title_field": meta.title_field,
		"is_submittable": cint(meta.is_submittable),
		"is_single": cint(meta.issingle),
		"fields": fields,
		"restricted_fields": restricted,
		"_docs_touched": f"DocType: {doctype}",
		"_result_summary": f"{len(fields)} field(s)",
	}


def _permission_target(doctype: str) -> str:
	"""Whose read permission answers for this doctype.

	A child table carries no permissions of its own — its rows are read through
	the document they belong to — so asking frappe about the child answers False
	for everybody but the Administrator, and the describe would refuse the users
	it was opened for. The blueprint is the document those rows live on, so it is
	the one to ask.
	"""
	return BLUEPRINT if doctype in describable_child_tables() else doctype


def _may_run(report: str) -> bool:
	"""Whether this user is one of the roles the report allows, if it names any."""
	try:
		return bool(frappe.get_doc("Report", report).is_permitted())
	except frappe.PermissionError:
		return False


def _page_result(rows: list[dict], key: str, start: int, limit: int) -> dict:
	page = rows[start : start + limit]
	return {
		key: page,
		"count": len(page),
		"total": len(rows),
		"start": start,
		"truncated": start + limit < len(rows),
		"_result_summary": f"{len(page)} of {len(rows)} {key.rstrip('s')}(s)",
	}


def _page(payload: dict) -> tuple[int, int]:
	start = max(0, cint(payload.get("start")))
	limit = cint(payload.get("limit")) or DEFAULT_LIMIT
	return start, max(1, min(limit, MAX_LIMIT))


def _query(payload: dict) -> str:
	return _optional_str(payload, "query").lower()


def _optional_str(payload: dict, key: str) -> str:
	value = payload.get(key)
	if value is None:
		return ""
	if not isinstance(value, str):
		raise ValueError(f"{key} must be a string.")
	return value.strip()


def _require_str(payload: dict, key: str) -> str:
	value = payload.get(key)
	if not isinstance(value, str) or not value.strip():
		raise ValueError(f"{key} is required and must be a string.")
	return value.strip()


PAGE_ARGS: dict[str, Any] = {
	"start": {"type": "integer", "description": "Rows to skip. Default 0."},
	"limit": {
		"type": "integer",
		"description": f"Rows to return, 1 to {MAX_LIMIT}. Default {DEFAULT_LIMIT}.",
	},
}


TOOLS = [
	{
		"tool_name": "list_site_doctypes",
		"handler_path": "frappe_agents.tools.builder_tools.list_site_doctypes",
		"capability": CAPABILITY_READ,
		"description": (
			"List the doctypes on this site — name, module and description — so you can name "
			"real records in a blueprint. This is the site's shape, not its contents: it "
			"returns no documents. The list is what the current user may read, minus the "
			"doctypes no agent may ever be granted. Use it before suggesting an access rule, "
			"and read the site's own labels rather than guessing at names."
		),
		"args_schema": {
			"type": "object",
			"properties": {
				"query": {
					"type": "string",
					"description": "Optional text to match against the doctype name or its description.",
				},
				"module": {"type": "string", "description": "Optional module name to match."},
				**PAGE_ARGS,
			},
		},
	},
	{
		"tool_name": "list_site_reports",
		"handler_path": "frappe_agents.tools.builder_tools.list_site_reports",
		"capability": CAPABILITY_READ,
		"description": (
			"List the saved reports the current user may run — name, type and the doctype "
			"they report on. Use it when a job is 'give me these numbers every week': a "
			"report rule grants running one existing report, and nothing else."
		),
		"args_schema": {
			"type": "object",
			"properties": {
				"query": {"type": "string", "description": "Optional text to match against the report name."},
				**PAGE_ARGS,
			},
		},
	},
	{
		"tool_name": "describe_site_doctype",
		"handler_path": "frappe_agents.tools.builder_tools.describe_site_doctype",
		"capability": CAPABILITY_READ,
		"description": (
			"Describe one doctype's fields — fieldname, label, fieldtype, options, required — "
			"so a blueprint can say what the agent would actually work with. Reads no "
			"documents. Fields above the base permission level are named under "
			"restricted_fields and not described: an agent is never granted those. "
			"A doctype you cannot describe refuses in the same words whichever reason "
			"it was, so do not read a refusal as evidence about the site."
		),
		"args_schema": {
			"type": "object",
			"properties": {
				"doctype": {"type": "string", "description": "DocType to describe."},
			},
			"required": ["doctype"],
		},
	},
]
