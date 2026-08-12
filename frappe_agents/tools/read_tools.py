"""Read-only tools.

Every handler runs as `frappe.session.user` — the effective user of the run — and
never changes it. Data is reached through the permission-checked ORM only:
`frappe.get_list`, `frappe.get_doc` after `has_permission`, `frappe.get_meta`.
No `frappe.get_all`, no `frappe.db.sql`, no `ignore_permissions` in this file.
"""

from typing import Any

import frappe
from frappe.model import no_value_fields
from frappe.utils import cint

from frappe_agents.tools.base import CAPABILITY_READ, ToolDenied

MAX_LIMIT = 50
DEFAULT_LIMIT = 20
MAX_REPORT_ROWS = 200
MAX_LISTED_DOCS = 20
RESTRICTED = "<restricted>"

STANDARD_FIELDS = (
	"name",
	"owner",
	"creation",
	"modified",
	"modified_by",
	"docstatus",
	"idx",
)


def search_documents(args: dict) -> dict:
	"""List documents of one doctype the current user is allowed to see."""
	doctype = _require_str(args, "doctype")
	_require_read(doctype)

	meta = frappe.get_meta(doctype)
	allowed_levels = meta.get_permlevel_access("read")

	fields, restricted = _resolve_fields(meta, args.get("fields"), allowed_levels)
	filters = _resolve_filters(meta, args.get("filters"), allowed_levels)
	limit = _resolve_limit(args.get("limit"))
	order_by = _resolve_order_by(meta, args.get("order_by"), allowed_levels)

	rows = frappe.get_list(
		doctype,
		filters=filters,
		fields=fields,
		order_by=order_by,
		limit_page_length=limit,
	)

	# Restricted fields come back marked, never silently dropped: the model has to
	# be able to tell "you cannot see this" from "this is empty".
	for row in rows:
		for fieldname in restricted:
			row[fieldname] = RESTRICTED

	names = [row.get("name") for row in rows if row.get("name")]
	return {
		"doctype": doctype,
		"fields": list(fields) + list(restricted),
		"restricted_fields": restricted,
		"rows": rows,
		"count": len(rows),
		"limit": limit,
		"_docs_touched": _docs_touched(doctype, names),
	}


def get_doctype_meta(args: dict) -> dict:
	"""Describe the fields of a doctype the current user is allowed to read."""
	doctype = _require_str(args, "doctype")
	_require_read(doctype)

	meta = frappe.get_meta(doctype)
	allowed_levels = meta.get_permlevel_access("read")

	fields = []
	restricted = []
	for df in meta.fields:
		if df.fieldtype in no_value_fields:
			continue
		if cint(df.permlevel) not in allowed_levels:
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
		"title_field": meta.title_field,
		"is_submittable": cint(meta.is_submittable),
		"fields": fields,
		"restricted_fields": restricted,
		"_docs_touched": f"DocType: {doctype}",
	}


def run_report(args: dict) -> dict:
	"""Run a saved report as the current user and return its rows."""
	report_name = _require_str(args, "report_name")
	filters = args.get("filters") or {}
	if not isinstance(filters, dict):
		raise ValueError("filters must be an object of fieldname to value.")

	if not frappe.has_permission("Report", "read"):
		raise ToolDenied("You are not allowed to read reports.")

	try:
		report = frappe.get_doc("Report", report_name)
	except frappe.DoesNotExistError:
		raise ValueError(f"No such report: {report_name}")

	if cint(report.get("disabled")):
		raise ToolDenied(f"Report {report_name} is disabled.")

	if not frappe.has_permission("Report", "read", doc=report):
		raise ToolDenied(f"You are not allowed to read the report {report_name}.")

	if not report.is_permitted():
		raise ToolDenied(f"You are not allowed to run the report {report_name}.")

	if report.ref_doctype and not frappe.has_permission(report.ref_doctype, "report"):
		raise ToolDenied(f"You are not allowed to report on {report.ref_doctype}.")

	from frappe.desk.query_report import run as run_query_report

	response = (
		run_query_report(
			report_name=report_name,
			filters=filters,
			user=frappe.session.user,
			ignore_prepared_report=True,
		)
		or {}
	)

	result = response.get("result") or []
	total_rows = len(result)
	truncated = total_rows > MAX_REPORT_ROWS

	return {
		"report": report_name,
		"columns": response.get("columns") or [],
		"rows": result[:MAX_REPORT_ROWS],
		"total_rows": total_rows,
		"truncated": truncated,
		"_docs_touched": f"Report: {report_name}",
	}


def _require_read(doctype: str) -> None:
	if not frappe.has_permission(doctype, "read"):
		raise ToolDenied(f"You are not allowed to read {doctype}.")


def _require_str(args: dict, key: str) -> str:
	value = args.get(key)
	if not isinstance(value, str) or not value.strip():
		raise ValueError(f"{key} is required and must be a string.")
	return value.strip()


def _resolve_fields(meta: Any, requested: Any, allowed_levels: list) -> tuple[list[str], list[str]]:
	if requested is None:
		requested = _default_fields(meta)
	if isinstance(requested, str):
		requested = [requested]
	if not isinstance(requested, list):
		raise ValueError("fields must be a list of fieldnames.")

	fields: list[str] = []
	restricted: list[str] = []
	for raw in requested:
		if not isinstance(raw, str):
			raise ValueError("fields must be a list of fieldnames.")
		fieldname = raw.strip()
		if fieldname in STANDARD_FIELDS:
			if fieldname not in fields:
				fields.append(fieldname)
			continue
		df = meta.get_field(fieldname)
		if not df:
			raise ValueError(f"Unknown field {fieldname} on {meta.name}.")
		if df.fieldtype in no_value_fields:
			continue
		if cint(df.permlevel) not in allowed_levels:
			if fieldname not in restricted:
				restricted.append(fieldname)
			continue
		if fieldname not in fields:
			fields.append(fieldname)

	if "name" not in fields:
		fields.insert(0, "name")
	return fields, restricted


def _default_fields(meta: Any) -> list[str]:
	fields = ["name"]
	if meta.title_field:
		fields.append(meta.title_field)
	for df in meta.fields:
		if len(fields) >= 6:
			break
		if cint(df.in_list_view) and df.fieldtype not in no_value_fields:
			fields.append(df.fieldname)
	fields.append("modified")
	return fields


def _resolve_filters(meta: Any, filters: Any, allowed_levels: list) -> dict:
	if filters is None:
		return {}
	if not isinstance(filters, dict):
		raise ValueError("filters must be an object of fieldname to value.")

	for fieldname in filters:
		if fieldname in STANDARD_FIELDS:
			continue
		df = meta.get_field(fieldname)
		if not df:
			raise ValueError(f"Unknown filter field {fieldname} on {meta.name}.")
		if cint(df.permlevel) not in allowed_levels:
			raise ToolDenied(f"You are not allowed to filter {meta.name} on {fieldname}.")
	return filters


def _resolve_limit(limit: Any) -> int:
	limit = cint(limit) or DEFAULT_LIMIT
	return max(1, min(limit, MAX_LIMIT))


def _resolve_order_by(meta: Any, order_by: Any, allowed_levels: list) -> str:
	if not order_by:
		return "modified desc"
	if not isinstance(order_by, str):
		raise ValueError("order_by must be a string like 'modified desc'.")

	parts = order_by.replace(",", " ").split()
	if len(parts) > 2:
		raise ValueError("order_by must be one field and an optional asc or desc.")

	fieldname = parts[0].strip("`").split(".")[-1]
	direction = parts[1].lower() if len(parts) == 2 else "desc"
	if direction not in ("asc", "desc"):
		raise ValueError("order_by direction must be asc or desc.")

	if fieldname not in STANDARD_FIELDS:
		df = meta.get_field(fieldname)
		if not df or df.fieldtype in no_value_fields:
			raise ValueError(f"Cannot order by {fieldname} on {meta.name}.")
		if cint(df.permlevel) not in allowed_levels:
			raise ToolDenied(f"You are not allowed to order {meta.name} by {fieldname}.")

	return f"{fieldname} {direction}"


def _docs_touched(doctype: str, names: list) -> str | None:
	if not names:
		return None
	listed = ", ".join(str(name) for name in names[:MAX_LISTED_DOCS])
	if len(names) > MAX_LISTED_DOCS:
		listed += f", +{len(names) - MAX_LISTED_DOCS} more"
	return f"{doctype}: {listed}"


TOOLS = [
	{
		"tool_name": "search_documents",
		"handler_path": "frappe_agents.tools.read_tools.search_documents",
		"capability": CAPABILITY_READ,
		"description": (
			"List documents of one doctype. Returns only rows and fields the current user "
			"is allowed to read; fields above the user's permission level come back as "
			"'<restricted>'."
		),
		"args_schema": {
			"type": "object",
			"properties": {
				"doctype": {"type": "string", "description": "DocType to search, e.g. 'Task'."},
				"filters": {
					"type": "object",
					"description": (
						'Field filters, e.g. {"status": "Open"} or {"creation": [">", "2026-01-01"]}.'
					),
				},
				"fields": {
					"type": "array",
					"items": {"type": "string"},
					"description": "Fieldnames to return. Defaults to a short summary set.",
				},
				"limit": {
					"type": "integer",
					"description": f"Rows to return, 1 to {MAX_LIMIT}. Default {DEFAULT_LIMIT}.",
				},
				"order_by": {
					"type": "string",
					"description": "One field and a direction, e.g. 'modified desc'.",
				},
			},
			"required": ["doctype"],
		},
	},
	{
		"tool_name": "get_doctype_meta",
		"handler_path": "frappe_agents.tools.read_tools.get_doctype_meta",
		"capability": CAPABILITY_READ,
		"description": (
			"Describe a doctype's fields — fieldname, label, fieldtype, options, required — "
			"so you can build a correct search. Fields the user may not read are listed by "
			"name under restricted_fields."
		),
		"args_schema": {
			"type": "object",
			"properties": {
				"doctype": {"type": "string", "description": "DocType to describe."},
			},
			"required": ["doctype"],
		},
	},
	{
		"tool_name": "run_report",
		"handler_path": "frappe_agents.tools.read_tools.run_report",
		"capability": CAPABILITY_READ,
		"description": (
			"Run a saved Frappe report as the current user and return its columns and rows. "
			f"Rows are capped at {MAX_REPORT_ROWS}; check 'truncated' and 'total_rows'."
		),
		"args_schema": {
			"type": "object",
			"properties": {
				"report_name": {"type": "string", "description": "Name of the Report record."},
				"filters": {"type": "object", "description": "Report filters as an object."},
			},
			"required": ["report_name"],
		},
	},
]
