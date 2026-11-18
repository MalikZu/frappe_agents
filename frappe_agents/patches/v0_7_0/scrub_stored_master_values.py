# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Take back master values stored on extractions that their readers could not see.

A sensitive flag on a Document Extraction may carry the linked master record's
own value, for example the IBAN the supplier record holds. Before the gate
checked permlevel, that value was kept whenever the field was not masked, so a
person who could read the supplier but not its bank fields still had the value
on their own extraction row.

New readings keep it only when the reader may see the field. This blanks it on
rows written before that, for every master field that sits above permlevel 0 or
is masked. That rule does not depend on who reads the row, so it needs no user:
it may blank a value the reader was in fact allowed to see, and that only costs
them a convenience. The mismatch boolean is kept, because it is the safety
signal and it says nothing about the value.

Each row is rewritten only when something changed, with `update_modified=False`:
the extraction did not change, its record did. A second run finds nothing to do.

Read with `get_all` in batches of `BATCH`, keyed on the name. This is migration
code with no user behind it, not tool code.
"""

import frappe

EXTRACTION = "Document Extraction"
BATCH = 200


def execute() -> None:
	last = ""
	while True:
		rows = frappe.get_all(
			EXTRACTION,
			filters={"sensitive_flags": ("is", "set"), "name": (">", last)},
			fields=["name", "sensitive_flags"],
			order_by="name asc",
			limit=BATCH,
		)
		if not rows:
			return
		for row in rows:
			scrub_row(row.name, row.sensitive_flags)
		last = rows[-1].name


def scrub_row(name: str, raw: str | None) -> bool:
	"""Blank the protected master values on one row. Says whether it changed it."""
	try:
		flags = frappe.parse_json(raw) or {}
	except Exception:
		return False
	if not isinstance(flags, dict):
		return False

	changed = False
	for key, flag in flags.items():
		if not isinstance(flag, dict) or flag.get("master_value") in (None, ""):
			continue
		if not protected(flag.get("master_doctype"), _fieldname(key)):
			continue
		flag["master_value"] = None
		flag["master_visible"] = 0
		changed = True

	if changed:
		frappe.db.set_value(EXTRACTION, name, "sensitive_flags", frappe.as_json(flags), update_modified=False)
	return changed


def protected(doctype: str | None, fieldname: str) -> bool:
	"""Whether the framework restricts this field for some readers: permlevel or mask.

	A master doctype that no longer exists cannot be checked, so its value is
	treated as protected: blanking a convenience is the safe direction.
	"""
	if not doctype or not frappe.db.exists("DocType", doctype):
		return True
	df = frappe.get_meta(doctype).get_field(fieldname)
	if not df:
		return True
	# `mask` is a v16 docfield property; on version-15 it is simply absent.
	return bool(df.permlevel) or bool(df.get("mask"))


def _fieldname(key: str) -> str:
	"""A flag key is a fieldname, or `table[index].fieldname` for a child row."""
	return key.rsplit(".", 1)[-1]
