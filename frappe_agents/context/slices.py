"""Context slices: one narrow view of a document, fetched on request.

`get_slice` is the only entry point. Every slice starts at the same gate — read
permission on the focal document — and every slice is capped. Nothing here
returns a document dump.

Two permission shapes live side by side, and the difference matters:

- **Rows that belong to the focal document** (its timeline, its versions, its
  attachments) are fetched with `frappe.get_all` behind the focal-doc gate,
  exactly as frappe core's `get_docinfo` does. `get_list` cannot be used here:
  it raises PermissionError on Comment and Version for anyone who is not a
  System Manager, silently drops every email row on Communication for users
  without a User Email, and narrows ToDo to the user's own assignments. Each
  such call is marked `# focal-doc-gated` and carries an explicit limit,
  because `get_all` has no default page length.
- **Linked documents** are different documents. They go through `get_list`, and
  every single row is then re-checked with `frappe.has_permission(..., doc=...)`,
  because `get_list` never runs per-row `has_permission` hooks. What the user
  may not see is reported as a count, never as content.
"""

import json
from typing import Any

import frappe
from frappe.model import no_value_fields, table_fields
from frappe.utils import cint

from frappe_agents.context.untrusted import is_untrusted_fieldtype, wrap
from frappe_agents.tools.base import ToolDenied

RESTRICTED = "<restricted>"
MASKED = "<masked>"

# docstatus never reaches the model as an integer.
DOCSTATUS_WORDS = {0: "DRAFT", 1: "SUBMITTED", 2: "CANCELLED"}

SLICES = ("fields", "child_table", "timeline", "versions", "links", "attachments")

MAX_CHILD_ROWS = 100
MAX_TIMELINE = 20
MAX_VERSIONS = 10
MAX_VERSION_CHANGES = 10
MAX_LINK_ROWS = 20
MAX_LINK_DOCTYPES = 15
MAX_ATTACHMENTS = 50
MAX_TEXT_CHARS = 2_000
MAX_VALUE_CHARS = 200
COUNT_CAP = 100

COMMUNICATION_FIELDS = (
	"name",
	"creation",
	"communication_date",
	"communication_medium",
	"subject",
	"sender",
	"sender_full_name",
	"content",
)

# Child-row keys that describe the row's place in the table, not its data.
ROW_META_KEYS = frozenset(
	{
		"name",
		"owner",
		"creation",
		"modified",
		"modified_by",
		"parent",
		"parentfield",
		"parenttype",
		"idx",
		"docstatus",
		"doctype",
	}
)


def get_slice(doctype: str, name: str, slice: str, **params: Any) -> dict:
	"""One slice of one document, for a user who is allowed to read it."""
	doctype = _require_str(doctype, "doctype")
	name = _require_str(name, "name")
	handler = _HANDLERS.get((slice or "").strip())
	if not handler:
		raise ValueError(f"Unknown slice {slice!r}. Known slices: {', '.join(SLICES)}.")

	require_read(doctype, name)

	payload = handler(doctype, name, params)
	payload.setdefault("_docs_touched", f"{doctype}: {name}")
	payload.update({"doctype": doctype, "name": name, "slice": slice})
	return payload


def require_read(doctype: str, name: str) -> None:
	"""The focal-document gate. Everything a slice returns hangs off this one check."""
	if not frappe.db.exists(doctype, name):
		raise ValueError(f"No such document: {doctype} {name}")
	if not frappe.has_permission(doctype, "read", doc=name):
		raise ToolDenied(f"You are not allowed to read {doctype} {name}.")


def status_word(docstatus: Any) -> str:
	"""DRAFT, SUBMITTED or CANCELLED — the model never sees the integer."""
	return DOCSTATUS_WORDS.get(cint(docstatus), "DRAFT")


def _slice_fields(doctype: str, name: str, params: dict) -> dict:
	meta = frappe.get_meta(doctype)
	doc = frappe.get_doc(doctype, name)
	values, restricted = project_fields(meta, doc, f"{doctype} {name}")
	values["status_word"] = status_word(doc.get("docstatus"))
	return {
		"fields": values,
		"restricted_fields": restricted,
		"child_tables": [df.fieldname for df in meta.get_table_fields()],
	}


def _slice_child_table(doctype: str, name: str, params: dict) -> dict:
	table = _require_str(params.get("table"), "table")
	meta = frappe.get_meta(doctype)

	df = meta.get_field(table)
	if not df or df.fieldtype not in table_fields:
		raise ValueError(f"{table} is not a child table on {doctype}.")
	if cint(df.permlevel) not in meta.get_permlevel_access("read"):
		raise ToolDenied(f"You are not allowed to read {table} on {doctype}.")

	limit = _limit(params.get("limit"), MAX_CHILD_ROWS)
	doc = frappe.get_doc(doctype, name)
	rows = doc.get(table) or []
	child_meta = frappe.get_meta(df.options)

	projected = []
	restricted: list[str] = []
	for row in rows[:limit]:
		values, row_restricted = project_fields(
			child_meta,
			row,
			f"{doctype} {name} {table}",
			parenttype=doctype,
			text_chars=MAX_VALUE_CHARS,
		)
		values["idx"] = row.get("idx")
		projected.append(values)
		for fieldname in row_restricted:
			if fieldname not in restricted:
				restricted.append(fieldname)

	return {
		"table": table,
		"child_doctype": df.options,
		"rows": projected,
		"row_count": len(rows),
		"returned": len(projected),
		"truncated": len(rows) > len(projected),
		"restricted_fields": restricted,
	}


def _slice_timeline(doctype: str, name: str, params: dict) -> dict:
	limit = _limit(params.get("limit"), MAX_TIMELINE)
	entries = _comments(doctype, name, limit) + _communications(doctype, name, limit)
	entries.sort(key=lambda entry: str(entry.get("when") or ""), reverse=True)
	entries = entries[:limit]
	return {"entries": entries, "count": len(entries), "limit": limit}


def _slice_versions(doctype: str, name: str, params: dict) -> dict:
	meta = frappe.get_meta(doctype)
	if not cint(getattr(meta, "track_changes", 0)):
		# No track_changes means no Version row can exist. Do not query.
		return {"tracked": False, "versions": [], "count": 0}

	limit = _limit(params.get("limit"), MAX_VERSIONS)
	rows = frappe.get_all(  # focal-doc-gated: see AGENTS.md ORM rule
		"Version",
		fields=["name", "owner", "creation", "data"],
		filters={"ref_doctype": doctype, "docname": str(name)},
		order_by="creation desc",
		limit=limit,
	)
	versions = [_version_entry(meta, row, doctype, name) for row in rows]
	return {"tracked": True, "versions": versions, "count": len(versions)}


def _slice_links(doctype: str, name: str, params: dict) -> dict:
	direction = (params.get("direction") or "all").strip().lower()
	if direction not in ("up", "down", "all"):
		raise ValueError("direction must be up, down or all.")

	limit = _limit(params.get("limit"), MAX_LINK_ROWS)
	payload: dict = {"direction": direction}
	invisible = 0
	invisible_doctypes: list[str] = []

	if direction in ("up", "all"):
		up = _links_up(doctype, name, limit)
		payload["up"] = up["groups"]
		invisible += up["invisible_count"]
		invisible_doctypes += up["invisible_doctypes"]

	if direction in ("down", "all"):
		down = _links_down(doctype, name, limit)
		payload["down"] = down["groups"]
		invisible += down["invisible_count"]
		invisible_doctypes += down["invisible_doctypes"]

	payload["not_visible"] = {
		"count": invisible,
		"doctypes": sorted(set(invisible_doctypes)),
	}
	return payload


def _slice_attachments(doctype: str, name: str, params: dict) -> dict:
	limit = _limit(params.get("limit"), MAX_ATTACHMENTS)
	# File's own permission check is doctype-level, so a get_list here would hand
	# back private attachments of documents the user cannot read. The focal-doc
	# gate is the correct check: these files belong to this document.
	rows = frappe.get_all(  # focal-doc-gated: see AGENTS.md ORM rule
		"File",
		fields=["name", "file_name", "file_size", "is_private", "file_type"],
		filters={
			"attached_to_doctype": doctype,
			"attached_to_name": str(name),
			"is_folder": 0,
		},
		order_by="creation desc",
		limit=limit,
	)

	files = [
		{
			"name": row.get("name"),
			"file_name": _truncate(row.get("file_name"), MAX_VALUE_CHARS),
			"file_size": cint(row.get("file_size")),
			"is_private": bool(cint(row.get("is_private"))),
			"file_type": row.get("file_type"),
		}
		for row in rows
	]
	return {"attachments": files, "count": len(files), "limit": limit}


_HANDLERS = {
	"fields": _slice_fields,
	"child_table": _slice_child_table,
	"timeline": _slice_timeline,
	"versions": _slice_versions,
	"links": _slice_links,
	"attachments": _slice_attachments,
}


def _comments(doctype: str, name: str, limit: int) -> list[dict]:
	rows = frappe.get_all(  # focal-doc-gated: see AGENTS.md ORM rule
		"Comment",
		fields=["name", "creation", "content", "owner", "comment_type"],
		filters={
			"reference_doctype": doctype,
			"reference_name": str(name),
			"comment_type": "Comment",
		},
		order_by="creation desc",
		limit=limit,
	)
	return [
		{
			"type": "comment",
			"id": row.get("name"),
			"when": row.get("creation"),
			"by": row.get("owner"),
			"content": wrap(_truncate(row.get("content"), MAX_TEXT_CHARS), f"Comment {row.get('name')}"),
		}
		for row in rows
	]


def _communications(doctype: str, name: str, limit: int) -> list[dict]:
	# Communication attaches two ways: reference_doctype/reference_name, and the
	# timeline_links child table. Query both and union, or half the thread is missing.
	rows = frappe.get_all(  # focal-doc-gated: see AGENTS.md ORM rule
		"Communication",
		fields=list(COMMUNICATION_FIELDS),
		filters={
			"reference_doctype": doctype,
			"reference_name": str(name),
			"communication_type": "Communication",
		},
		order_by="communication_date desc",
		limit=limit,
	)

	seen = {row.get("name") for row in rows}
	linked = frappe.get_all(  # focal-doc-gated: see AGENTS.md ORM rule
		"Communication",
		fields=list(COMMUNICATION_FIELDS),
		filters=[
			["Communication Link", "link_doctype", "=", doctype],
			["Communication Link", "link_name", "=", str(name)],
			["Communication", "communication_type", "=", "Communication"],
		],
		order_by="communication_date desc",
		limit=limit,
		distinct=True,
	)
	rows += [row for row in linked if row.get("name") not in seen]

	entries = []
	for row in rows:
		subject = (row.get("subject") or "").strip()
		body = row.get("content") or ""
		text = f"{subject}\n\n{body}" if subject else body
		entries.append(
			{
				"type": "communication",
				"id": row.get("name"),
				"when": row.get("communication_date") or row.get("creation"),
				"by": row.get("sender_full_name") or row.get("sender"),
				"medium": row.get("communication_medium"),
				"content": wrap(_truncate(text, MAX_TEXT_CHARS), f"Communication {row.get('name')}"),
			}
		)
	return entries


def _version_entry(meta: Any, row: dict, doctype: str, name: str) -> dict:
	entry = {
		"version": row.get("name"),
		"by": row.get("owner"),
		"when": row.get("creation"),
	}

	try:
		data = json.loads(row.get("data") or "{}")
	except Exception:
		data = {}
	if not isinstance(data, dict):
		data = {}

	diff_keys = ("changed", "added", "removed", "row_changed")
	if not any(data.get(key) for key in diff_keys):
		# Version.for_insert writes a different, much smaller shape.
		entry["event"] = "created"
		return entry

	source = f"{doctype} {name}"
	changes: list[dict] = []

	for change in data.get("changed") or []:
		if len(change) < 3:
			continue
		changes.append(_field_change(meta, change[0], change[1], change[2], source))

	for change in data.get("row_changed") or []:
		# The real order is [table_fieldname, row_index, row_name, inner_changes] —
		# index before name, whatever the upstream docstring says.
		if len(change) < 4:
			continue
		table, index, inner = change[0], change[1], change[3]
		child_meta = _child_meta(meta, table)
		for inner_change in inner or []:
			if len(inner_change) < 3:
				continue
			field_change = _field_change(
				child_meta, inner_change[0], inner_change[1], inner_change[2], source
			)
			field_change["field"] = f"{table}[{cint(index)}].{field_change['field']}"
			changes.append(field_change)

	rows_added = [_row_change(change) for change in data.get("added") or []]
	rows_removed = [_row_change(change) for change in data.get("removed") or []]

	entry["changes"] = changes[:MAX_VERSION_CHANGES]
	entry["changes_truncated"] = len(changes) > MAX_VERSION_CHANGES
	if rows_added:
		entry["rows_added"] = rows_added[:MAX_VERSION_CHANGES]
	if rows_removed:
		entry["rows_removed"] = rows_removed[:MAX_VERSION_CHANGES]
	return entry


def _field_change(meta: Any, fieldname: str, old: Any, new: Any, source: str) -> dict:
	if fieldname == "docstatus":
		return {"field": fieldname, "from": status_word(old), "to": status_word(new)}

	df = meta.get_field(fieldname) if meta else None
	return {
		"field": fieldname,
		"from": _version_value(df, old, source, fieldname),
		"to": _version_value(df, new, source, fieldname),
	}


def _version_value(df: Any, value: Any, source: str, fieldname: str) -> Any:
	if value is None or value == "":
		return value
	text = _truncate(value, MAX_VALUE_CHARS)
	if df is not None and is_untrusted_fieldtype(df.fieldtype):
		return wrap(text, f"{source} {fieldname}")
	return text


def _row_change(change: Any) -> dict:
	# added/removed carry the entire child row. Report which fields the row
	# carried, never the values — that blob is exactly what a slice must not emit.
	if not change or len(change) < 2 or not isinstance(change[1], dict):
		return {"table": change[0] if change else None}
	row = change[1]
	fields = [key for key in row if key not in ROW_META_KEYS]
	return {
		"table": change[0],
		"row": row.get("name"),
		"fields": sorted(fields)[:MAX_VERSION_CHANGES],
	}


def _child_meta(meta: Any, table: str) -> Any:
	df = meta.get_field(table) if table else None
	if not df or not df.options:
		return None
	try:
		return frappe.get_meta(df.options)
	except Exception:
		return None


def _links_up(doctype: str, name: str, limit: int) -> dict:
	"""Documents this one points at, through its own Link and Dynamic Link fields."""
	meta = frappe.get_meta(doctype)
	doc = frappe.get_doc(doctype, name)
	allowed = meta.get_permlevel_access("read")

	groups: dict[str, dict] = {}
	invisible = 0
	invisible_doctypes: list[str] = []
	seen: set[tuple[str, str]] = set()

	for df in meta.fields:
		if df.fieldtype not in ("Link", "Dynamic Link"):
			continue
		if cint(df.permlevel) not in allowed:
			continue
		value = doc.get(df.fieldname)
		if not value:
			continue

		target_doctype = doc.get(df.options) if df.fieldtype == "Dynamic Link" else df.options
		if not target_doctype or (target_doctype, str(value)) in seen:
			continue
		seen.add((target_doctype, str(value)))

		group = groups.setdefault(
			target_doctype,
			{"doctype": target_doctype, "docs": [], "visible_count": 0, "invisible_count": 0},
		)
		if len(group["docs"]) >= limit:
			continue

		summary = _document_summary(target_doctype, value)
		if summary is None:
			invisible += 1
			group["invisible_count"] += 1
			invisible_doctypes.append(target_doctype)
			continue
		summary["field"] = df.fieldname
		group["docs"].append(summary)
		group["visible_count"] += 1

	return {
		"groups": [groups[key] for key in sorted(groups)],
		"invisible_count": invisible,
		"invisible_doctypes": invisible_doctypes,
	}


def _links_down(doctype: str, name: str, limit: int) -> dict:
	"""Documents that point at this one, per linked doctype."""
	groups = []
	invisible = 0
	invisible_doctypes: list[str] = []

	for linked_doctype, info in link_targets(doctype)[:MAX_LINK_DOCTYPES]:
		filters, or_filters = link_filters(doctype, name, info)
		rows = visible_rows(linked_doctype, filters, or_filters, limit)
		total = total_count(linked_doctype, filters, or_filters)

		docs = []
		for row in rows:
			summary = _document_summary(linked_doctype, row.get("name"))
			if summary is not None:
				docs.append(summary)

		hidden = 0
		if total is not None:
			hidden = max(0, cint(total) - len(docs))
		elif len(rows) > len(docs):
			hidden = len(rows) - len(docs)

		if not docs and not hidden:
			continue

		groups.append(
			{
				"doctype": linked_doctype,
				"docs": docs,
				"visible_count": len(docs),
				"invisible_count": hidden,
			}
		)
		if hidden:
			invisible += hidden
			invisible_doctypes.append(linked_doctype)

	return {
		"groups": groups,
		"invisible_count": invisible,
		"invisible_doctypes": invisible_doctypes,
	}


def may_read(doctype: str, name: Any) -> bool:
	"""Read permission on one document, per-row `has_permission` hooks included."""
	if not name:
		return False
	try:
		return bool(frappe.has_permission(doctype, "read", doc=name))
	except Exception:
		return False


def has_row_permission_hook(doctype: str) -> bool:
	"""True when a doctype registers a per-row `has_permission` hook.

	`get_list` never runs those hooks, so on these doctypes — File, Communication,
	ToDo, Note, Event, Contact, Address, User and a dozen more — a listed row is
	not proof the user may read it. Whoever lists them re-checks them.
	"""
	try:
		return bool((frappe.get_hooks("has_permission") or {}).get(doctype))
	except Exception:
		return False


def _document_summary(doctype: str, name: Any) -> dict | None:
	"""Name, title and status of one linked document — None when the user may not read it."""
	if not may_read(doctype, name):
		return None

	fields = ["name", "docstatus"]
	title_field = _title_field(doctype)
	if title_field and title_field not in fields:
		fields.append(title_field)

	try:
		row = frappe.db.get_value(doctype, name, fields, as_dict=True)
	except Exception:
		row = None
	if not row:
		return None

	summary = {
		"doctype": doctype,
		"name": row.get("name"),
		"status_word": status_word(row.get("docstatus")),
	}
	if title_field:
		summary["title"] = _truncate(row.get(title_field), MAX_VALUE_CHARS)
	return summary


def _title_field(doctype: str) -> str | None:
	try:
		meta = frappe.get_meta(doctype)
	except Exception:
		return None
	title_field = meta.title_field
	if not title_field or title_field == "name":
		return None
	df = meta.get_field(title_field)
	if not df or cint(df.permlevel) not in meta.get_permlevel_access("read"):
		return None
	return title_field


def link_targets(doctype: str) -> list[tuple[str, dict]]:
	"""The link shape for a doctype: which doctypes point at it, through which fields.

	`get_linked_doctypes` is Redis-cached and returns shape only. Its sibling
	`get_linked_docs` is deliberately not used — it materialises every row name of
	every linked doctype with no limit.
	"""
	from frappe.desk.form.linked_with import get_linked_doctypes

	try:
		linkinfo = get_linked_doctypes(doctype) or {}
	except Exception:
		return []

	targets = []
	for linked_doctype, info in sorted(linkinfo.items()):
		if not isinstance(info, dict) or info.get("get_parent"):
			continue
		if not info.get("fieldname"):
			continue
		targets.append((linked_doctype, info))
	return targets


def link_filters(doctype: str, name: str, info: dict) -> tuple[list, list]:
	"""Filters selecting the rows of one linked doctype that point at this document."""
	child = info.get("child_doctype")
	fieldnames = info.get("fieldname") or []
	if isinstance(fieldnames, str):
		fieldnames = [fieldnames]

	or_filters = [
		[child, fieldname, "=", str(name)] if child else [fieldname, "=", str(name)]
		for fieldname in fieldnames
	]

	filters = []
	doctype_fieldname = info.get("doctype_fieldname")
	if doctype_fieldname:
		filters.append(
			[child, doctype_fieldname, "=", doctype] if child else [doctype_fieldname, "=", doctype]
		)
	return filters, or_filters


def visible_rows(linked_doctype: str, filters: list, or_filters: list, limit: int) -> list[dict]:
	"""Rows of a linked doctype the user may list. Permission-checked, always capped."""
	try:
		return frappe.get_list(
			linked_doctype,
			filters=filters or None,
			or_filters=or_filters or None,
			fields=["name"],
			limit=limit,
			distinct=True,
			order_by="modified desc",
		)
	except frappe.PermissionError:
		return []
	except Exception:
		return []


def total_count(linked_doctype: str, filters: list, or_filters: list) -> int | None:
	"""Raw row total for one linked doctype, permissions bypassed.

	`frappe.db.count` is a plain COUNT with no permission conditions, so its value
	is arithmetic input for `invisible = total - visible` and nothing else. It is
	never returned as content and never decides what a user may see.

	It takes no or_filters, so a doctype linked through several fields is counted
	once per field and summed. Overlapping rows make that an upper bound, which is
	why the caller clamps the difference at zero.
	"""
	try:
		if not or_filters:
			return cint(frappe.db.count(linked_doctype, filters=filters or None))
		total = 0
		for condition in or_filters:
			total += cint(frappe.db.count(linked_doctype, filters=[*filters, condition]))
		return total
	except Exception:
		return None


def project_fields(
	meta: Any,
	doc: Any,
	source: str,
	parenttype: str | None = None,
	docfields: Any = None,
	text_chars: int = MAX_TEXT_CHARS,
) -> tuple[dict, list[str]]:
	"""Field values for one document, permlevel-filtered and untrusted-wrapped.

	A field the user may not read comes back as `<restricted>`, never dropped: the
	model has to be able to tell "you cannot see this" from "this is empty".
	"""
	allowed = meta.get_permlevel_access("read", parenttype=parenttype)
	masked = masked_fieldnames(meta)

	values: dict = {"name": doc.get("name")}
	restricted: list[str] = []

	for df in meta.fields if docfields is None else docfields:
		if df.fieldtype in no_value_fields or df.fieldtype in table_fields:
			continue
		if cint(df.permlevel) not in allowed:
			values[df.fieldname] = RESTRICTED
			restricted.append(df.fieldname)
			continue
		if df.fieldname in masked:
			values[df.fieldname] = MASKED
			continue
		values[df.fieldname] = field_value(df, doc.get(df.fieldname), source, text_chars)

	return values, restricted


def field_value(df: Any, value: Any, source: str, text_chars: int = MAX_TEXT_CHARS) -> Any:
	"""One field value, with authored text wrapped as untrusted."""
	if value is None or value == "":
		return value
	if is_untrusted_fieldtype(df.fieldtype):
		return wrap(_truncate(value, text_chars), f"{source} {df.fieldname}")
	return value


def masked_fieldnames(meta: Any) -> set[str]:
	"""Fields the framework masks. A masked value looks like data, so it gets a label."""
	getter = getattr(meta, "get_masked_fields", None)
	if not getter:
		return set()
	try:
		fields = getter() or []
	except Exception:
		return set()
	return {getattr(field, "fieldname", field) for field in fields}


def _limit(value: Any, cap: int) -> int:
	limit = cint(value) or cap
	return max(1, min(limit, cap))


def _require_str(value: Any, label: str) -> str:
	if not isinstance(value, str) or not value.strip():
		raise ValueError(f"{label} is required and must be a string.")
	return value.strip()


def _truncate(value: Any, limit: int) -> Any:
	if value is None:
		return None
	text = str(value)
	if len(text) <= limit:
		return text
	return text[: limit - 3] + "..."
