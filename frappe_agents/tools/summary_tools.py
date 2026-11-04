"""Counts and totals by group, as the running user.

"How many open tickets per team" is a question a page of rows answers wrong: a
model that counts the rows it was handed states the page size as the total. This
tool asks the database instead, through `frappe.get_list` as
`frappe.session.user`, so the user's role permissions, User Permissions and
permission query conditions shape the numbers exactly as they shape a list view.
No `frappe.get_all`, no `frappe.db.sql`, no `frappe.db.count`, no
`ignore_permissions` in this file.

An aggregate is a read of every row it covers, so it is held to more than a list:

- **Every field is checked before the query runs.** A group or an aggregate on a
  field above the user's permission level, a masked field, a field an
  administrator marked sensitive, or a Password field is refused. So is a filter
  on one of those, because a count under a filter answers "how many have this
  value", one question at a time.
- **Doctypes with a per-row `has_permission` hook are refused.** `get_list`
  never runs those hooks, so a count there would include documents the user may
  not open. `search_documents` re-checks such rows one by one; a total cannot.
- **Small groups keep their count and lose their values.** A sum over one or two
  documents is those documents' values. Below `MIN_GROUP_SIZE`, sum, avg, min and
  max come back null and the row says it was suppressed.
"""

from typing import Any

import frappe
from frappe.model import no_value_fields, table_fields
from frappe.utils import cint

from frappe_agents.context.slices import has_row_permission_hook, masked_fieldnames, status_word
from frappe_agents.context.untrusted import UNTRUSTED_FIELDTYPES
from frappe_agents.extraction.gate import sensitive_fieldnames
from frappe_agents.tools.base import CAPABILITY_READ, ToolDenied, row_cap
from frappe_agents.tools.read_tools import (
	_as_bool,
	_require_read,
	_require_str,
	_resolve_filters,
	_resolve_limit,
)

MAX_GROUPS = 50
MAX_GROUP_BY = 2
MAX_AGGREGATES = 4
# Below this many documents a group's sum, average, minimum and maximum are the
# documents' own values, so they are withheld. The count stays.
MIN_GROUP_SIZE = 3

FN_COUNT = "count"
FN_SUM = "sum"
FN_AVG = "avg"
FN_MIN = "min"
FN_MAX = "max"
FUNCTIONS = (FN_COUNT, FN_SUM, FN_AVG, FN_MIN, FN_MAX)

NUMERIC_FIELDTYPES = frozenset({"Int", "Float", "Currency", "Percent"})
ORDERED_FIELDTYPES = NUMERIC_FIELDTYPES | {"Date", "Datetime"}
FIELDTYPES_FOR = {
	FN_SUM: NUMERIC_FIELDTYPES,
	FN_AVG: NUMERIC_FIELDTYPES,
	FN_MIN: ORDERED_FIELDTYPES,
	FN_MAX: ORDERED_FIELDTYPES,
}
# Short values that name a category. Long text is not a category, and grouping
# on it would hand back authored text as group labels.
GROUPABLE_FIELDTYPES = frozenset(
	{"Data", "Link", "Dynamic Link", "Select", "Check", "Date", "Int", "Autocomplete", "Rating"}
)
GROUPABLE_STANDARD_FIELDS = ("owner", "modified_by", "docstatus")
ORDERED_STANDARD_FIELDS = ("creation", "modified")


def summarize_documents(payload: dict) -> dict:
	"""Count and total documents of one doctype, grouped by up to two fields."""
	doctype = _require_str(payload, "doctype")
	_require_read(doctype)

	meta = frappe.get_meta(doctype)
	_refuse_doctype(meta)

	allowed_levels = meta.get_permlevel_access("read")
	withheld = _withheld_fields(meta)

	group_by = _resolve_group_by(meta, payload.get("group_by"), allowed_levels, withheld)
	aggregates = _resolve_aggregates(meta, payload.get("aggregates"), allowed_levels, withheld)
	filters = _resolve_filters(meta, payload.get("filters"), allowed_levels)
	_refuse_withheld_filters(meta, filters, withheld)
	limit = _resolve_limit(payload.get("limit") or MAX_GROUPS, row_cap(doctype, MAX_GROUPS))
	include_cancelled = _as_bool(payload.get("include_cancelled"))

	# The same rule as search_documents: a cancelled document was undone, and a
	# total that includes it states a number that stopped being true.
	if cint(meta.is_submittable) and not include_cancelled and "docstatus" not in filters:
		filters = dict(filters, docstatus=("!=", 2))

	select = [_aggregate_field(fn, field, alias) for fn, field, alias in aggregates]

	if group_by:
		rows = frappe.get_list(
			doctype,
			filters=filters,
			fields=[*group_by, *select],
			group_by=", ".join(group_by),
			order_by=f"{aggregates[0][2]} desc",
			limit=limit + 1,
		)
		truncated = len(rows) > limit
		rows = rows[:limit]
		total = _single_row(doctype, filters, select)
	else:
		total = _single_row(doctype, filters, select)
		rows = [total]
		truncated = False

	suppressed = sum(_suppress_small(row, aggregates) for row in rows)
	_suppress_small(total, aggregates)
	# The database ordered small groups by the values just withheld. Their place
	# in the list would still say how large those values were, so they go last.
	rows.sort(key=lambda row: row["suppressed"])
	if "docstatus" in group_by:
		# docstatus is an integer nobody should have to decode.
		for row in rows:
			row["status_word"] = status_word(row.pop("docstatus", 0))

	columns = [("status_word" if field == "docstatus" else field) for field in group_by]
	columns += [alias for _fn, _field, alias in aggregates]

	return {
		"doctype": doctype,
		"group_by": group_by,
		"columns": columns,
		"rows": rows,
		"returned": len(rows),
		"truncated": truncated,
		"limit": limit,
		"total": _clean_total(total, aggregates),
		"filters": filters,
		"include_cancelled": include_cancelled,
		"min_group_size": MIN_GROUP_SIZE,
		"suppressed_groups": suppressed if group_by else 0,
		"_result_summary": _summary(doctype, group_by, len(rows), truncated),
		"_docs_touched": f"DocType: {doctype}",
	}


def _aggregate_field(fn: str, field: str | None, alias: str) -> Any:
	"""One aggregate column in the shape `frappe.get_list` accepts.

	Frappe v16's query builder refuses SQL functions written as strings and takes
	a dict instead. This is the only place that knows the syntax.
	"""
	return {fn.upper(): field or "*", "as": alias}


def _single_row(doctype: str, filters: dict, select: list) -> dict:
	rows = frappe.get_list(doctype, filters=filters, fields=select, limit=1)
	return dict(rows[0]) if rows else {FN_COUNT: 0}


def _refuse_doctype(meta: Any) -> None:
	if has_row_permission_hook(meta.name):
		raise ToolDenied(
			f"summarize_documents cannot total {meta.name}: who may read each {meta.name} is "
			"decided document by document, and a count would include documents you may not "
			"open. Use search_documents, which checks every row."
		)
	if cint(meta.issingle):
		raise ValueError(f"{meta.name} is a single document, not a list. Use search_documents.")
	if cint(meta.istable):
		raise ValueError(f"{meta.name} is a child table. Summarize its parent doctype instead.")
	if cint(getattr(meta, "is_virtual", 0)):
		raise ValueError(f"{meta.name} is not stored in the database and cannot be totalled.")


def _withheld_fields(meta: Any) -> set[str]:
	"""Fields no aggregate may touch: masked, marked sensitive, or a Password."""
	fields = {df.fieldname for df in meta.fields if df.fieldtype == "Password"}
	fields |= masked_fieldnames(meta)
	fields |= sensitive_fieldnames(meta.name)
	return fields


def _resolve_group_by(meta: Any, requested: Any, allowed_levels: list, withheld: set) -> list[str]:
	if requested in (None, "", []):
		return []
	if isinstance(requested, str):
		requested = [part for part in requested.split(",") if part.strip()]
	if not isinstance(requested, list) or not all(isinstance(item, str) for item in requested):
		raise ValueError("group_by must be a list of fieldnames.")

	fields: list[str] = []
	for raw in requested:
		fieldname = raw.strip()
		if fieldname in fields:
			continue
		if fieldname not in GROUPABLE_STANDARD_FIELDS:
			df = _checked_field(meta, fieldname, allowed_levels, withheld, "group")
			if df.fieldtype not in GROUPABLE_FIELDTYPES:
				raise ValueError(
					f"Cannot group {meta.name} by {fieldname}: a {df.fieldtype} field is not a category."
				)
		fields.append(fieldname)

	if len(fields) > MAX_GROUP_BY:
		raise ValueError(f"group_by takes at most {MAX_GROUP_BY} fields.")
	return fields


def _resolve_aggregates(
	meta: Any, requested: Any, allowed_levels: list, withheld: set
) -> list[tuple[str, str | None, str]]:
	"""`[(fn, field, alias)]`, validated, always with a count. None asked means a count."""
	if requested in (None, []):
		return [(FN_COUNT, None, FN_COUNT)]
	if isinstance(requested, dict):
		requested = [requested]
	if not isinstance(requested, list):
		raise ValueError('aggregates must be a list like [{"fn": "sum", "field": "grand_total"}].')
	if len(requested) > MAX_AGGREGATES:
		raise ValueError(f"aggregates takes at most {MAX_AGGREGATES} entries.")

	resolved: list[tuple[str, str | None, str]] = []
	for item in requested:
		if not isinstance(item, dict):
			raise ValueError('Each aggregate must be an object like {"fn": "count"}.')
		fn = str(item.get("fn") or "").strip().lower()
		if fn not in FUNCTIONS:
			raise ValueError(f"Unknown aggregate {fn!r}. Use one of: {', '.join(FUNCTIONS)}.")

		if fn == FN_COUNT:
			# A count counts documents. A field on it would only invite the
			# question of whether empty values were counted.
			entry = (FN_COUNT, None, FN_COUNT)
		else:
			fieldname = item.get("field")
			if not isinstance(fieldname, str) or not fieldname.strip():
				raise ValueError(f"{fn} needs a field.")
			fieldname = fieldname.strip()
			_check_aggregate_field(meta, fn, fieldname, allowed_levels, withheld)
			entry = (fn, fieldname, f"{fn}_{fieldname}")

		if entry not in resolved:
			resolved.append(entry)

	# The count always rides along, last unless it was asked for: it is what
	# decides whether a group is too small to show its values.
	if not any(fn == FN_COUNT for fn, _field, _alias in resolved):
		resolved.append((FN_COUNT, None, FN_COUNT))
	return resolved


def _check_aggregate_field(meta: Any, fn: str, fieldname: str, allowed_levels: list, withheld: set) -> None:
	if fieldname in ORDERED_STANDARD_FIELDS:
		if fn in (FN_MIN, FN_MAX):
			return
		raise ValueError(f"{fn} needs a number field; {fieldname} is a date.")
	df = _checked_field(meta, fieldname, allowed_levels, withheld, fn)
	if df.fieldtype not in FIELDTYPES_FOR[fn]:
		kind = "a number field" if fn in (FN_SUM, FN_AVG) else "a number or date field"
		raise ValueError(f"{fn} needs {kind}; {fieldname} on {meta.name} is {df.fieldtype}.")


def _checked_field(meta: Any, fieldname: str, allowed_levels: list, withheld: set, use: str) -> Any:
	"""The field, once it is known to exist and to be one the user may aggregate."""
	df = meta.get_field(fieldname)
	if not df or df.fieldtype in no_value_fields or df.fieldtype in table_fields:
		raise ValueError(f"Unknown field {fieldname} on {meta.name}.")
	if cint(df.permlevel) not in allowed_levels:
		raise ToolDenied(f"You are not allowed to {use} {meta.name} by {fieldname}.")
	if fieldname in withheld:
		raise ToolDenied(f"{fieldname} on {meta.name} is sensitive and cannot be summarized.")
	if df.fieldtype in UNTRUSTED_FIELDTYPES:
		raise ValueError(f"Cannot {use} {meta.name} by {fieldname}: it is free text.")
	return df


def _refuse_withheld_filters(meta: Any, filters: dict, withheld: set) -> None:
	# A count under a filter answers "how many have this value". Asked often
	# enough, that recovers the value itself.
	for fieldname in filters:
		if fieldname in withheld:
			raise ToolDenied(f"You may not filter a summary of {meta.name} on {fieldname}: it is sensitive.")


def _suppress_small(row: dict, aggregates: list) -> bool:
	"""Null a small group's values in place. True when anything was withheld."""
	value_aliases = [alias for fn, _field, alias in aggregates if fn != FN_COUNT]
	if not value_aliases or cint(row.get(FN_COUNT)) >= MIN_GROUP_SIZE:
		row["suppressed"] = False
		return False
	for alias in value_aliases:
		row[alias] = None
	row["suppressed"] = True
	return True


def _clean_total(total: dict, aggregates: list) -> dict:
	clean = {alias: total.get(alias) for _fn, _field, alias in aggregates}
	clean["suppressed"] = bool(total.get("suppressed"))
	return clean


def _summary(doctype: str, group_by: list, returned: int, truncated: bool) -> str:
	if not group_by:
		return f"totals of {doctype}"
	text = f"{returned} group(s) of {doctype} by {', '.join(group_by)}"
	return text + ", truncated" if truncated else text


TOOLS = [
	{
		"tool_name": "summarize_documents",
		"handler_path": "frappe_agents.tools.summary_tools.summarize_documents",
		"capability": CAPABILITY_READ,
		"description": (
			"Count and total documents of one doctype, optionally grouped by one or two fields. "
			"Use it for every 'how many' or 'how much' question: 'open tickets by team', 'spend by "
			"supplier this year', 'draft invoices this month'. Never count search_documents rows "
			"for that, because a page of rows is not a total. Numbers cover only documents the "
			"current user may read, and cancelled documents are left out unless you pass "
			f"include_cancelled. At most {MAX_GROUPS} groups come back, largest first; 'total' "
			"is the figure across all of them, and 'truncated' says whether groups were left out. "
			f"A group of fewer than {MIN_GROUP_SIZE} documents shows its count but no sum, "
			"average, minimum or maximum ('suppressed' is true): use search_documents to read "
			"those documents. Refused on doctypes whose access is checked document by document, "
			"such as ToDo, File or Communication: use search_documents there."
		),
		"args_schema": {
			"type": "object",
			"properties": {
				"doctype": {
					"type": "string",
					"description": "DocType to summarize, e.g. 'Purchase Invoice'.",
				},
				"filters": {
					"type": "object",
					"description": (
						'Field filters, e.g. {"status": "Open"} or {"posting_date": [">=", "2026-01-01"]}.'
					),
				},
				"group_by": {
					"type": "array",
					"items": {"type": "string"},
					"description": (
						f"Up to {MAX_GROUP_BY} fieldnames to group by, e.g. ['supplier']. Short "
						"fields only: Link, Select, Data, Check, Date, Int, or owner. Leave out for "
						"one total."
					),
				},
				"aggregates": {
					"type": "array",
					"description": (
						f"Up to {MAX_AGGREGATES} of: count, sum, avg, min, max. sum and avg need a "
						"number field; min and max take a number or date field; count takes no "
						'field. E.g. [{"fn": "count"}, {"fn": "sum", "field": "grand_total"}]. '
						"Defaults to count. Rows are ordered by the first one, largest first."
					),
					"items": {
						"type": "object",
						"properties": {
							"fn": {"type": "string", "enum": list(FUNCTIONS)},
							"field": {"type": "string"},
						},
						"required": ["fn"],
					},
				},
				"limit": {
					"type": "integer",
					"description": f"Groups to return, 1 to {MAX_GROUPS}. Default {MAX_GROUPS}.",
				},
				"include_cancelled": {
					"type": "boolean",
					"description": (
						"Include cancelled documents. Default false. Only set it when the "
						"question is about what was cancelled."
					),
				},
			},
			"required": ["doctype"],
		},
	},
]
