"""The sensitive-field gate, and the two duplicate checks.

This is the part of extraction that exists because of one attack: a genuine-looking
invoice with the payment details changed. Everything else on the document can be
wrong and a reviewer will catch it by reading; a bank account cannot be checked by
reading, only by comparing.

So the gate does two things, and does them by construction rather than by warning:

1. **Withholds.** A field named in `Agent Settings.sensitive_fields` is pulled out
   of the values before anything is written. `withhold` returns the draft values
   and the flags separately, and the pipeline only ever writes the first of those.
2. **Compares.** When the extraction resolved a link to a master record that
   carries the same fieldname, the master's value is fetched and compared here,
   server-side. The reviewer gets a boolean they cannot miss.

Two details of that comparison are not optional:

* The master value is read with `frappe.get_doc`, never `frappe.db.get_value` or
  `get_all`. v16 masks fields per user in the query layer — a masked field comes
  back as `"XXXXXXXX"`, which would make every extraction mismatch forever. Alert
  fatigue on the one alert that must never be ignored is worse than no alert.
* A master value the reviewer is not permitted to see unmasked is not written into
  the flags. `sensitive_flags` is a field on a document they load; putting a masked
  value there would leak it through the form, the API and the version history. The
  boolean is the safety signal, the value is only a convenience.

Sensitivity comes from `Agent Settings` and nowhere else. Core knows no ERPNext
fieldnames; a field is sensitive because an administrator said so.

The duplicate checks below carry the same obligation from the other side. They
have to look wider than the person they run for — a duplicate is by definition
somebody else's row — so every candidate they find is permission-checked before
it is named. What the person may read comes back with its identity; what they
may not comes back as a count and nothing else, the way `context/slices.py`
reports rows the permission system hid. A duplicate flag that named another
user's extraction, the document it created, or a target record they cannot open
would be an existence oracle wearing a helpful label.
"""

from typing import Any

import frappe
from frappe.utils import cint

MAX_DUPLICATE_ROWS = 5
MAX_UNIQUE_CHECKS = 10
# Files over one set of bytes. Frappe dedupes the blob, so the same invoice
# attached in twenty places is twenty rows and one hash. The list is a join key
# and never a result, but `get_all` has no default page length, so it is capped.
MAX_FILE_ROWS = 100
VALUE_CHARS = 200

EXTRACTION_DT = "Document Extraction"

# Extractions that already produced something a person can act on. A discarded or
# failed run of the same file is not a duplicate, it is a retry.
LIVE_STATUSES = ("Needs Review", "Accepted")


def sensitive_fieldnames(doctype: str) -> set[str]:
	"""Fieldnames an administrator marked sensitive on this doctype."""
	settings = frappe.get_cached_doc("Agent Settings")
	rows = settings.get("sensitive_fields") or []
	return {row.fieldname for row in rows if row.get("document_type") == doctype and row.get("fieldname")}


def withhold(spec: dict, values: dict, links: dict | None = None) -> tuple[dict, dict]:
	"""Split extracted values into what may be written and what must be confirmed.

	Returns `(draft_values, flags)`. `draft_values` is what the pipeline writes —
	it never contains a sensitive field. `flags` is
	`{key: {value, master_doctype, master_name, master_value, mismatch, confirmed}}`
	keyed by fieldname, or by `table[index].fieldname` for a value inside a child
	row: a payment detail hidden in a line item is the same attack with more steps.
	"""
	links = links or {}
	draft: dict[str, Any] = {}
	flags: dict[str, dict] = {}

	sensitive = set(spec.get("sensitive") or [])
	for fieldname, value in values.items():
		table = (spec.get("child_tables") or {}).get(fieldname)
		if table:
			draft[fieldname] = _withhold_rows(spec, fieldname, table, value, flags)
			continue
		if fieldname in sensitive:
			flags[fieldname] = _flag(spec, fieldname, value, links)
			continue
		draft[fieldname] = value

	return draft, flags


def _withhold_rows(spec: dict, fieldname: str, table: dict, rows: Any, flags: dict) -> list:
	sensitive = set(table.get("sensitive") or [])
	if not isinstance(rows, list):
		return []
	clean_rows = []
	for index, row in enumerate(rows):
		clean_row = {}
		for key, value in (row or {}).items():
			if key in sensitive:
				flags[f"{fieldname}[{index}].{key}"] = _blank_flag(value)
				continue
			clean_row[key] = value
		clean_rows.append(clean_row)
	return clean_rows


def _flag(spec: dict, fieldname: str, value: Any, links: dict) -> dict:
	flag = _blank_flag(value)
	master = _master(spec.get("doctype"), fieldname, links)
	if not master:
		return flag

	doctype, name, master_value, visible = master
	flag["master_doctype"] = doctype
	flag["master_name"] = name
	# Only the boolean is unconditional. The value follows it only when this user
	# would have been shown the real thing anyway.
	flag["master_value"] = _cut(master_value) if visible else None
	flag["master_visible"] = 1 if visible else 0
	if master_value:
		flag["mismatch"] = 0 if _same(value, master_value) else 1
	return flag


def _blank_flag(value: Any) -> dict:
	return {
		"value": _cut(value),
		"master_doctype": None,
		"master_name": None,
		"master_value": None,
		"master_visible": 0,
		"mismatch": 0,
		"confirmed": 0,
	}


def _master(target_doctype: str | None, fieldname: str, links: dict) -> tuple | None:
	"""The linked record that carries this same fieldname, if one resolved.

	Generic on purpose: the master is whichever Link field on the target resolved to
	a single record whose doctype has a field of the same name. That is how a
	supplier's stored IBAN meets the IBAN printed on the invoice, without core ever
	knowing either word.
	"""
	if not target_doctype or not links:
		return None

	meta = frappe.get_meta(target_doctype)
	for df in meta.get_link_fields():
		name = links.get(df.fieldname)
		if not name or not df.options:
			continue
		try:
			master_meta = frappe.get_meta(df.options)
		except frappe.DoesNotExistError:
			continue
		if not master_meta.get_field(fieldname):
			continue
		if not frappe.has_permission(df.options, "read", doc=name):
			continue
		try:
			doc = frappe.get_doc(df.options, name)
		except frappe.DoesNotExistError:
			continue
		# get_doc, never db.get_value: the query layer masks per user and would hand
		# back "XXXXXXXX" as if the master really said that.
		return df.options, name, doc.get(fieldname), fieldname not in _masked_fields(master_meta)

	return None


def _masked_fields(meta: Any) -> set[str]:
	try:
		return {df.fieldname for df in meta.get_masked_fields()}
	except AttributeError:
		return set()


def find_duplicates(
	target_doctype: str, content_hash: str | None, values: dict, spec: dict, exclude: str | None = None
) -> dict:
	"""The two duplicate checks that need no knowledge of any particular doctype.

	Same file, already extracted into this doctype; and a `unique` field whose
	extracted value already exists. Both are flags for a human, never a refusal —
	a supplier really can send the same invoice twice, and the reviewer is the one
	who knows whether this is that.

	Two keys per check, not one. `same_file` and `unique_collisions` are the
	candidates this person may read, named; `*_hidden_count` is how many more
	there were that they may not. A hidden candidate is still a flag — "somebody
	already extracted these bytes" is worth knowing — but it arrives without an
	extraction name, a created document, a target name or a value.
	"""
	same_file, same_file_hidden = _same_file(target_doctype, content_hash, exclude)
	collisions, collisions_hidden = _unique_collisions(target_doctype, values, spec)
	return {
		"same_file": same_file,
		"same_file_hidden_count": same_file_hidden,
		"unique_collisions": collisions,
		"unique_collisions_hidden_count": collisions_hidden,
	}


def _same_file(target_doctype: str, content_hash: str | None, exclude: str | None) -> tuple[list[dict], int]:
	if not content_hash:
		# Remote files have no hash and legacy rows may not either. No hash is "cannot
		# tell", never "not a duplicate".
		return [], 0

	# A content-addressed lookup whose result never leaves this function: these names
	# are the join key for the extraction query below and nothing else. Marked and
	# capped the way `context/slices.py` marks its gated reads. Oldest first, because
	# the row a duplicate points back to is the earliest one over these bytes.
	files = frappe.get_all(  # hash-keyed join key, never returned: see AGENTS.md ORM rule
		"File",
		filters={"content_hash": content_hash},
		pluck="name",
		order_by="creation asc",
		limit_page_length=MAX_FILE_ROWS,
	)
	if not files:
		return [], 0

	filters = {
		"source_file": ("in", files),
		"target_doctype": target_doctype,
		"status": ("in", LIVE_STATUSES),
	}
	if exclude:
		filters["name"] = ("!=", exclude)

	# `get_list` cannot be used here: it would drop the rows this person may not
	# read, and a dropped duplicate is a duplicate nobody was told about. They are
	# fetched as candidates and each one is permission-checked below, so a hidden
	# row can be counted instead of named.
	rows = frappe.get_all(  # candidates only, checked one by one below: see AGENTS.md ORM rule
		EXTRACTION_DT,
		filters=filters,
		fields=["name", "status", "created_doc", "creation"],
		order_by="creation desc",
		limit_page_length=MAX_DUPLICATE_ROWS,
	)

	visible: list[dict] = []
	hidden = 0
	for row in rows:
		if not _readable(EXTRACTION_DT, row.name):
			hidden += 1
			continue
		visible.append(
			{
				"name": row.name,
				"status": row.status,
				# Being allowed to read the extraction is not being allowed to read what
				# it created: the draft carries the document's own permissions.
				"created_doc": row.created_doc if _readable(target_doctype, row.created_doc) else None,
				"creation": row.creation,
			}
		)

	return visible, hidden


def _unique_collisions(target_doctype: str, values: dict, spec: dict) -> tuple[list[dict], int]:
	meta = frappe.get_meta(target_doctype)
	readable_doctype = bool(frappe.has_permission(target_doctype, "read"))
	collisions: list[dict] = []
	hidden = 0
	checked = 0

	for fieldname, value in values.items():
		# The cap is on lookups, not on findings: ten queries is the budget whether
		# or not they collide.
		if checked >= MAX_UNIQUE_CHECKS:
			break
		if not isinstance(value, str) or not value.strip():
			continue
		df = meta.get_field(fieldname)
		if not df or not cint(df.unique):
			continue
		checked += 1

		existing = _readable_match(target_doctype, fieldname, value) if readable_doctype else None
		if existing:
			collisions.append({"fieldname": fieldname, "value": _cut(value), "name": existing})
		elif frappe.db.exists(target_doctype, {fieldname: value}):
			# The collision is real and the record is not theirs to see. Saying so costs
			# nothing that is not already public to them — a unique index refuses the
			# insert either way — while the name, the field and the value stay behind.
			hidden += 1

	return collisions, hidden


def _readable_match(target_doctype: str, fieldname: str, value: str) -> str | None:
	"""The colliding record, if this person may read it.

	`get_list` first, then `has_permission` on the row: `get_list` applies the
	permission query but never runs per-row `has_permission` hooks, which is the
	same reason `context/slices.py` re-checks every linked document it lists.
	"""
	names = frappe.get_list(target_doctype, filters={fieldname: value}, pluck="name", limit_page_length=1)
	name = names[0] if names else None
	return name if _readable(target_doctype, name) else None


def _readable(doctype: str, name: str | None) -> bool:
	if not name:
		return False
	try:
		return bool(frappe.has_permission(doctype, "read", doc=name))
	except frappe.DoesNotExistError:
		# A candidate deleted between the query and the check is not a duplicate to
		# report, and it is certainly not one to name.
		return False


def _same(left: Any, right: Any) -> bool:
	return _normalise(left) == _normalise(right)


def _normalise(value: Any) -> str:
	"""Compare on the characters, not the typography.

	Whitespace and case differ between how an IBAN is printed and how it is stored;
	nothing else is smoothed over, because every other difference is the difference
	we are looking for.
	"""
	return "".join(str(value or "").split()).upper()


def _cut(value: Any) -> Any:
	if isinstance(value, str):
		return value[:VALUE_CHARS]
	return value
