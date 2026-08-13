"""Link resolution: propose a master record, never create one.

A document says "Al Yazeem Trading LLC". A Link field wants a record name. Between
those two facts sits the one thing extraction must not do, which is decide. So:

* An exact, single, permitted match is filled in — there is nothing to decide.
* Anything else ships as candidates for the reviewer, and the field is left empty.
  An unresolved Link written through as raw text would fail validation on insert
  and take the whole draft with it.
* Nothing is ever created. Not a supplier, not an item, not a currency. A document
  from outside cannot add a master record to the site, and that is not a setting.

Every candidate goes through `frappe.get_list` (so the user's own permissions
filter the search) and is then re-checked with `has_permission` per row, because
`get_list` does not run per-row permission hooks.
"""

from typing import Any

import frappe

MAX_CANDIDATES = 5
SEARCH_LIMIT = 10
VALUE_CHARS = 200


def resolve_links(target_doctype: str, values: dict, spec: dict) -> dict:
	"""Match extracted text against existing records for every Link field.

	Returns `{"resolved": {fieldname: name}, "candidates": {key: {...}}}`. Keys are
	fieldnames, or `table[index].fieldname` for a link inside a child row.
	"""
	resolved: dict[str, str] = {}
	candidates: dict[str, dict] = {}

	meta = frappe.get_meta(target_doctype)
	_resolve_into(meta, values, "", resolved, candidates)

	for fieldname, table in (spec.get("child_tables") or {}).items():
		rows = values.get(fieldname)
		if not isinstance(rows, list):
			continue
		child_meta = frappe.get_meta(table.get("doctype"))
		for index, row in enumerate(rows):
			if isinstance(row, dict):
				_resolve_into(child_meta, row, f"{fieldname}[{index}].", resolved, candidates)

	return {"resolved": resolved, "candidates": candidates}


def apply_resolution(values: dict, resolution: dict, spec: dict) -> dict:
	"""Values with resolved links filled in and unresolved ones removed."""
	resolved = resolution.get("resolved") or {}
	candidates = resolution.get("candidates") or {}
	clean: dict[str, Any] = {}

	for fieldname, value in values.items():
		table = (spec.get("child_tables") or {}).get(fieldname)
		if table and isinstance(value, list):
			clean[fieldname] = [
				_apply_row(row, index, fieldname, resolved, candidates)
				for index, row in enumerate(value)
				if isinstance(row, dict)
			]
			continue
		if fieldname in resolved:
			clean[fieldname] = resolved[fieldname]
			continue
		if fieldname in candidates:
			# Unresolved: the text stays on the extraction row for the reviewer, and
			# the draft field stays empty rather than carrying an invalid link.
			continue
		clean[fieldname] = value

	return clean


def _apply_row(row: dict, index: int, fieldname: str, resolved: dict, candidates: dict) -> dict:
	clean = {}
	for key, value in row.items():
		path = f"{fieldname}[{index}].{key}"
		if path in resolved:
			clean[key] = resolved[path]
			continue
		if path in candidates:
			continue
		clean[key] = value
	return clean


def _resolve_into(meta: Any, values: dict, prefix: str, resolved: dict, candidates: dict) -> None:
	for df in meta.get_link_fields():
		value = values.get(df.fieldname)
		if not isinstance(value, str) or not value.strip():
			continue
		if not df.options:
			continue

		key = f"{prefix}{df.fieldname}"
		text = value.strip()

		if not frappe.has_permission(df.options, "read"):
			candidates[key] = {
				"doctype": df.options,
				"text": text[:VALUE_CHARS],
				"options": [],
				"note": f"You are not allowed to read {df.options}, so this was left empty.",
			}
			continue

		matches = _search(df.options, text)
		if len(matches) == 1:
			resolved[key] = matches[0]["name"]
			continue

		candidates[key] = {
			"doctype": df.options,
			"text": text[:VALUE_CHARS],
			"options": matches[:MAX_CANDIDATES],
			"note": (
				f"No {df.options} matched this text."
				if not matches
				else f"{len(matches)} records could be this {df.options}. Pick one."
			),
		}


def _search(doctype: str, text: str) -> list[dict]:
	"""Exact name first, then the doctype's own search fields, permission-filtered."""
	exact = _permitted(doctype, _list(doctype, {"name": text}))
	if exact:
		return exact[:1]

	meta = frappe.get_meta(doctype)
	fields = []
	if meta.get("title_field"):
		fields.append(meta.title_field)
	for fieldname in meta.get_search_fields() or []:
		if fieldname not in fields:
			fields.append(fieldname)

	seen: dict[str, dict] = {}
	for fieldname in ["name", *fields]:
		df = meta.get_field(fieldname)
		if fieldname != "name" and (
			not df or df.fieldtype not in ("Data", "Small Text", "Text", "Link", "Select")
		):
			continue
		for row in _permitted(doctype, _list(doctype, {fieldname: ("like", f"%{text}%")})):
			seen.setdefault(row["name"], row)
		if len(seen) > MAX_CANDIDATES:
			break

	return list(seen.values())


def _list(doctype: str, filters: dict) -> list[dict]:
	meta = frappe.get_meta(doctype)
	fields = ["name"]
	if meta.get("title_field") and meta.title_field != "name":
		fields.append(f"{meta.title_field} as title")

	try:
		rows = frappe.get_list(
			doctype,
			filters=filters,
			fields=fields,
			limit_page_length=SEARCH_LIMIT,
			ignore_ifnull=True,
		)
	except frappe.PermissionError:
		return []
	except Exception:
		# A doctype with an unusual query condition or a missing title field is a
		# resolution that did not happen, not an extraction that failed.
		frappe.logger("frappe_agents").debug(f"link search on {doctype} failed", exc_info=True)
		return []

	return [dict(row) for row in rows]


def _permitted(doctype: str, rows: list[dict]) -> list[dict]:
	"""get_list applies the query conditions but not the per-row permission hooks."""
	allowed = []
	for row in rows:
		if frappe.has_permission(doctype, "read", doc=row["name"]):
			allowed.append({"name": row["name"], "title": row.get("title") or row["name"]})
	return allowed
