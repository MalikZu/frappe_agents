"""The manifest: what exists around a document, as counts.

The manifest is the first thing an agent reads about a document, and it is
deliberately small — a few core fields and the shape of everything else. It says
"this document has 3 child tables, 14 comments, 2 attachments and 6 Sales Orders
pointing at it", so the model can ask for the one slice it needs instead of
being handed a dump it did not ask for.

Counts are permission-aware where it matters. Visible counts come from
`get_list`; the raw total comes from `frappe.db.count`, which bypasses
permissions entirely, and is used for one thing only: telling a permission
denial apart from a row the sample never reached. Documents the user may not see
are reported as a number and a doctype name — never as content.

Linked rows are sampled at `VISIBLE_SAMPLE_CAP`, so a count carries which kind of
hole it is: `not_visible_count` is a permission denial, `beyond_sample_count`
(with `sampled: true`) is a row the sample stopped short of. Only a denial puts a
doctype in the `not_visible` block.
"""

from typing import Any

import frappe
from frappe.model import no_value_fields, table_fields
from frappe.utils import cint

from frappe_agents.context.slices import (
	COUNT_CAP,
	link_filters,
	link_targets,
	project_fields,
	require_read,
	sample_linked_rows,
	status_word,
)

MAX_CORE_FIELDS = 10
MAX_CORE_TEXT_CHARS = 120
MAX_CHILD_TABLES = 20
MAX_LINK_DOCTYPES = 20
VISIBLE_SAMPLE_CAP = 50


def build_manifest(doctype: str, name: str) -> dict:
	"""What exists around one document, for a user who is allowed to read it."""
	doctype = _require_str(doctype, "doctype")
	name = _require_str(name, "name")
	require_read(doctype, name)

	meta = frappe.get_meta(doctype)
	doc = frappe.get_doc(doctype, name)
	links, not_visible = _links(doctype, name)

	return {
		"doctype": doctype,
		"name": name,
		"doc": _core(meta, doc, doctype, name),
		"slices": {
			"child_tables": _child_tables(meta, doc),
			"timeline": _timeline_counts(meta, doctype, name),
			"attachments": _attachment_counts(doctype, name),
			"links": links,
		},
		"not_visible": not_visible,
		"_docs_touched": f"{doctype}: {name}",
	}


def _core(meta: Any, doc: Any, doctype: str, name: str) -> dict:
	values, restricted = project_fields(
		meta,
		doc,
		f"{doctype} {name}",
		docfields=_core_docfields(meta),
		text_chars=MAX_CORE_TEXT_CHARS,
	)
	values["status_word"] = status_word(doc.get("docstatus"))
	values["owner"] = doc.get("owner")
	values["modified"] = doc.get("modified")
	if restricted:
		values["restricted_fields"] = restricted

	if cint(meta.is_submittable):
		if doc.get("amended_from"):
			values["amended_from"] = doc.get("amended_from")
		amended_by = _amended_by(doctype, name)
		if amended_by:
			values["amended_by"] = amended_by

	return values


def _core_docfields(meta: Any) -> list:
	"""The few fields that identify a document, not all of them."""
	chosen: list = []
	seen: set[str] = set()

	def take(df: Any) -> None:
		if not df or df.fieldname in seen:
			return
		if df.fieldtype in no_value_fields or df.fieldtype in table_fields:
			return
		seen.add(df.fieldname)
		chosen.append(df)

	if meta.title_field:
		take(meta.get_field(meta.title_field))

	for df in meta.fields:
		if len(chosen) >= MAX_CORE_FIELDS:
			break
		if cint(df.in_list_view) or cint(df.bold) or cint(df.in_standard_filter) or cint(df.reqd):
			take(df)

	return chosen[:MAX_CORE_FIELDS]


def _amended_by(doctype: str, name: str) -> str | None:
	"""The amendment of this document, when the user is allowed to see it."""
	try:
		rows = frappe.get_list(
			doctype,
			filters={"amended_from": name},
			fields=["name"],
			limit=1,
			order_by=None,
		)
	except Exception:
		return None
	return rows[0].get("name") if rows else None


def _child_tables(meta: Any, doc: Any) -> dict:
	allowed = meta.get_permlevel_access("read")
	tables = {}
	for df in meta.get_table_fields()[:MAX_CHILD_TABLES]:
		if cint(df.permlevel) not in allowed:
			continue
		tables[df.fieldname] = {
			"child_doctype": df.options,
			"rows": len(doc.get(df.fieldname) or []),
		}
	return tables


def _timeline_counts(meta: Any, doctype: str, name: str) -> dict:
	counts = {
		"comments": _count_rows(
			"Comment",
			{
				"reference_doctype": doctype,
				"reference_name": str(name),
				"comment_type": "Comment",
			},
		),
		"communications": _communication_count(doctype, name),
		# No track_changes means no Version row can exist. Do not query.
		"versions": (
			_count_rows("Version", {"ref_doctype": doctype, "docname": str(name)})
			if cint(getattr(meta, "track_changes", 0))
			else 0
		),
		"todos": _count_rows("ToDo", {"reference_type": doctype, "reference_name": str(name)}),
	}
	if any(value >= COUNT_CAP for value in counts.values()):
		counts["capped_at"] = COUNT_CAP
	return counts


def _communication_count(doctype: str, name: str) -> int:
	# Communication attaches by reference and through timeline links. Count both, once.
	names = set(
		_pluck(
			"Communication",
			{
				"reference_doctype": doctype,
				"reference_name": str(name),
				"communication_type": "Communication",
			},
		)
	)
	names.update(
		_pluck(
			"Communication",
			[
				["Communication Link", "link_doctype", "=", doctype],
				["Communication Link", "link_name", "=", str(name)],
				["Communication", "communication_type", "=", "Communication"],
			],
		)
	)
	return len(names)


def _attachment_counts(doctype: str, name: str) -> dict:
	# Attachments are plucked under the same cap as the timeline counts, so they need
	# the same marker: exactly COUNT_CAP means "at least this many", not "this many".
	count = _count_rows("File", _attachment_filters(doctype, name))
	counts = {"count": count}
	if count >= COUNT_CAP:
		counts["capped_at"] = COUNT_CAP
	return counts


def _attachment_filters(doctype: str, name: str) -> dict:
	return {
		"attached_to_doctype": doctype,
		"attached_to_name": str(name),
		"is_folder": 0,
	}


def _count_rows(doctype: str, filters: Any) -> int:
	return len(_pluck(doctype, filters))


def _pluck(doctype: str, filters: Any) -> list:
	"""Row names for one timeline source, capped. Behind the focal-doc gate."""
	try:
		return frappe.get_all(  # focal-doc-gated: see AGENTS.md ORM rule
			doctype,
			filters=filters,
			pluck="name",
			limit=COUNT_CAP,
			order_by=None,
			distinct=True,
		)
	except Exception:
		return []


def _links(doctype: str, name: str) -> tuple[dict, dict]:
	links: dict = {}
	not_visible_total = 0
	not_visible_doctypes: list[str] = []

	for linked_doctype, info in link_targets(doctype)[:MAX_LINK_DOCTYPES]:
		filters, or_filters = link_filters(doctype, name, info)
		sample = sample_linked_rows(linked_doctype, filters, or_filters, VISIBLE_SAMPLE_CAP)
		visible = len(sample["rows"])
		hidden = sample["not_visible"]
		beyond = sample["beyond_sample"]

		if not visible and not hidden and not beyond:
			continue

		entry = {"visible_count": visible, "not_visible_count": hidden}
		if sample["sampled"]:
			# The count stopped at the cap. Rows past it may well be readable, so they
			# are reported as unread, not as hidden.
			entry["sampled"] = True
			entry["sample_cap"] = VISIBLE_SAMPLE_CAP
			entry["beyond_sample_count"] = beyond
		links[linked_doctype] = entry

		if hidden:
			not_visible_total += hidden
			not_visible_doctypes.append(linked_doctype)

	return links, {"count": not_visible_total, "doctypes": not_visible_doctypes}


def _require_str(value: Any, label: str) -> str:
	if not isinstance(value, str) or not value.strip():
		raise ValueError(f"{label} is required and must be a string.")
	return value.strip()
