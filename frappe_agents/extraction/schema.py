"""DocType meta to JSON schema, in the one shape both provider families accept.

Frappe has no prior art here — no OpenAPI export, no JSON Schema anywhere — so the
mapping is written from the fieldtype table in `frappe.types.DF` and constrained by
what the providers actually enforce.

The shape is the load-bearing part, and it is not the obvious one:

* **Every property is required.** OpenAI strict mode demands it, and it drives
  Anthropic's optional-parameter count to zero — Anthropic 400s past 24 optional
  parameters, which any real invoice doctype would pass before lunch.
* **No nulls, no unions, no anyOf.** Anthropic 400s past 16 union-typed
  parameters. Absence is said with a sentinel — `""`, `0`, `[]` — and the
  description tells the model so.
* **`additionalProperties: false` on every object**, root and child rows alike.
* **No numeric or length constraints.** Anthropic rejects `minimum`, `maxLength`
  and friends outright. Constraints go in the description and are checked here.

Two more things travel in the schema because they cannot travel any other way:
confidence and page number. Anthropic returns 400 if citations are combined with
forced output, so there is no provider-side grounding available — these are the
model's own claim about its own work, and the review UI says so.

The schema is DocType field names and labels. Nothing extracted from a document
ever goes into it: schemas are cached provider-side for 24 hours.
"""

from typing import Any

import frappe
from frappe import _
from frappe.model import default_fields, no_value_fields, optional_fields, table_fields
from frappe.utils import cint, strip_html

from frappe_agents.extraction.gate import sensitive_fieldnames
from frappe_agents.tools.draft_tools import SYSTEM_FIELDS

# Grammar compilation has undocumented size limits and a documented 180s ceiling,
# so a 200-field doctype is refused rather than sent. Sensitive fields are never
# counted out: the gate cannot flag what the model was never asked to read.
MAX_FIELDS = 40
MAX_CHILD_TABLES = 3
MAX_CHILD_FIELDS = 12

DESCRIPTION_CHARS = 240
LABEL_CHARS = 120

STRING_FIELDTYPES = frozenset(
	{
		"Data",
		"Small Text",
		"Text",
		"Long Text",
		"Link",
		"Dynamic Link",
		"Select",
		"Date",
		"Datetime",
		"Time",
		"Phone",
		"Autocomplete",
		"Barcode",
	}
)
INT_FIELDTYPES = frozenset({"Int", "Long Int", "Check", "Duration"})
FLOAT_FIELDTYPES = frozenset({"Float", "Currency", "Percent", "Rating"})

# Nothing on a printed document fills these, and several of them would be actively
# wrong to ask a model for. Password is the loudest: it is stored encrypted and a
# model has no business proposing one.
NOT_EXTRACTABLE = frozenset(
	{
		"Password",
		"Signature",
		"Geolocation",
		"JSON",
		"Code",
		"Attach",
		"Attach Image",
		"Image",
		"Icon",
		"Color",
		"HTML Editor",
		"Markdown Editor",
		"Text Editor",
		"Table MultiSelect",
	}
)

# Hints that measurably improve extraction, from frappe's own Data field options.
DATA_OPTION_HINTS = {
	"Email": "An email address as printed.",
	"Phone": "A phone number as printed, digits and separators kept.",
	"URL": "A full URL as printed.",
	"IBAN": "An IBAN exactly as printed, spaces removed. Never correct or complete it.",
	"Name": "A person or entity name as printed.",
	"Barcode": "The barcode value as printed.",
}

EMPTY_STRING_NOTE = 'Use "" if it is not on the document.'
NUMBER_NOTE = "Use 0 if it is not on the document."
TABLE_NOTE = "Use [] if the document has no such rows."

FIELD_NOTES = "field_notes"
FIELDS = "fields"


def build_extraction_schema(target_doctype: str, user: str) -> dict:
	"""The schema for one doctype, as one user may write it.

	Returns `{"doctype", "schema", "fields", "child_tables", "sensitive"}` where
	`schema` is the dict handed to the provider and the rest is what the pipeline
	needs to read the answer back: fieldtypes, Select options for case-insensitive
	matching, and which fieldnames the gate will withhold.

	Fields the user may not write are absent from the schema, not filtered out of
	the answer afterwards. A field that never reaches the model cannot come back.
	"""
	meta = frappe.get_meta(target_doctype)
	if cint(meta.istable):
		frappe.throw(_("{0} is a child table. Extract into the parent doctype.").format(target_doctype))
	if cint(meta.issingle):
		frappe.throw(_("{0} is a settings document, not something to extract into.").format(target_doctype))

	sensitive = sensitive_fieldnames(target_doctype)
	permitted = set(meta.get_permitted_fieldnames(user=user, permission_type="write"))

	scalars = _pick(meta, permitted, sensitive, MAX_FIELDS)
	properties: dict[str, Any] = {}
	fields: dict[str, dict] = {}

	for df in scalars:
		properties[df.fieldname] = _property(df)
		fields[df.fieldname] = {
			"fieldtype": df.fieldtype,
			"options": df.options or "",
			"sensitive": df.fieldname in sensitive,
			"select_options": _select_options(df) if df.fieldtype == "Select" else [],
		}

	child_tables: dict[str, dict] = {}
	for df in _tables(meta, permitted):
		child = _child_schema(df, target_doctype, user)
		if not child:
			continue
		properties[df.fieldname] = child["schema"]
		child_tables[df.fieldname] = {
			"doctype": df.options,
			"fields": child["fields"],
			"sensitive": child["sensitive"],
		}

	if not properties:
		frappe.throw(
			_("You are not allowed to write any field on {0}, so there is nothing to extract.").format(
				target_doctype
			)
		)

	schema = {
		"type": "object",
		"properties": {
			FIELDS: {
				"type": "object",
				"description": _("Values read from the document, by fieldname."),
				"properties": properties,
				"required": sorted(properties),
				"additionalProperties": False,
			},
			FIELD_NOTES: _notes_property(),
		},
		"required": [FIELDS, FIELD_NOTES],
		"additionalProperties": False,
	}

	return {
		"doctype": target_doctype,
		"schema": schema,
		"fields": fields,
		"child_tables": child_tables,
		"sensitive": sorted(name for name in sensitive if name in fields),
	}


def normalise_values(spec: dict, extracted: dict) -> dict:
	"""The model's answer, turned into values a frappe document would accept.

	Unknown keys are dropped — a schema-conforming model cannot invent one, but a
	router that treats the schema as a hint can. Sentinel empties are dropped too:
	`""` means "not on the document", and writing it would overwrite a doctype
	default with a blank. Select values are matched case-insensitively, because no
	provider guarantees the casing of an enum it echoes back.
	"""
	values: dict[str, Any] = {}
	raw = extracted.get(FIELDS) if isinstance(extracted.get(FIELDS), dict) else {}

	for fieldname, meta in (spec.get("fields") or {}).items():
		if fieldname not in raw:
			continue
		value = _coerce(meta, raw.get(fieldname))
		if value is not None:
			values[fieldname] = value

	for fieldname, table in (spec.get("child_tables") or {}).items():
		rows = raw.get(fieldname)
		if not isinstance(rows, list):
			continue
		clean_rows = []
		for row in rows:
			if not isinstance(row, dict):
				continue
			clean_row = {}
			for child_fieldname, child_meta in (table.get("fields") or {}).items():
				if child_fieldname not in row:
					continue
				value = _coerce(child_meta, row.get(child_fieldname))
				if value is not None:
					clean_row[child_fieldname] = value
			if clean_row:
				clean_rows.append(clean_row)
		if clean_rows:
			values[fieldname] = clean_rows

	return values


def read_field_notes(spec: dict, extracted: dict) -> dict:
	"""`field_notes` as `{fieldname: {confidence, page}}`, for fields we asked for.

	Both numbers are the model's self-report. They are recorded so the reviewer can
	see where the model was unsure; they are never a reason to skip a confirmation.
	"""
	known = set(spec.get("fields") or {}) | set(spec.get("child_tables") or {})
	notes: dict[str, dict] = {}

	for note in extracted.get(FIELD_NOTES) or []:
		if not isinstance(note, dict):
			continue
		fieldname = str(note.get("fieldname") or "").strip()
		if fieldname not in known:
			continue
		confidence = _number(note.get("confidence"))
		notes[fieldname] = {
			"confidence": min(max(confidence, 0.0), 1.0),
			"page": max(cint(note.get("page")), 0),
		}

	return notes


def _pick(meta: Any, permitted: set, sensitive: set, limit: int) -> list:
	"""Extractable fields, most useful first, capped — sensitive ones always kept."""
	usable = [df for df in meta.fields if _is_extractable(df, permitted, sensitive)]

	must = [df for df in usable if df.fieldname in sensitive]
	rest = [df for df in usable if df.fieldname not in sensitive]
	rest.sort(key=_rank)

	room = max(limit - len(must), 0)
	chosen = must + rest[:room]
	order = {df.fieldname: index for index, df in enumerate(meta.fields)}
	chosen.sort(key=lambda df: order.get(df.fieldname, 0))
	return chosen


def _rank(df: Any) -> int:
	if cint(df.reqd):
		return 0
	if cint(df.bold) or cint(df.in_list_view):
		return 1
	if cint(df.in_standard_filter) or cint(df.search_index):
		return 2
	return 3


def _is_extractable(df: Any, permitted: set, sensitive: set) -> bool:
	if df.fieldname not in permitted:
		return False
	if df.fieldname in SYSTEM_FIELDS or df.fieldname in default_fields or df.fieldname in optional_fields:
		return False
	if df.fieldtype in NOT_EXTRACTABLE:
		return False
	if df.fieldtype in no_value_fields or df.fieldtype in table_fields:
		return False
	if cint(df.is_virtual):
		return False
	if (
		df.fieldtype not in STRING_FIELDTYPES
		and df.fieldtype not in INT_FIELDTYPES
		and df.fieldtype not in FLOAT_FIELDTYPES
	):
		return False

	# A sensitive field is asked for even when the form hides it or fetches it from
	# a master. It is never written by the pipeline, so the only thing extracting it
	# can do is show a reviewer that the document disagrees with the master — which
	# is the whole point. Skipping it here would put the redirected payment detail
	# in the one place nobody looks.
	if df.fieldname in sensitive:
		return True

	# Otherwise: a read-only field is filled by the doctype, not by us, and a fetched
	# field would be overwritten on save anyway.
	return not (cint(df.hidden) or cint(df.read_only) or df.fetch_from)


def _tables(meta: Any, permitted: set) -> list:
	rows = []
	for df in meta.get_table_fields():
		if df.fieldtype != "Table":
			continue
		# Not `permitted`: "Table" is one of frappe's no-value fieldtypes, so
		# `get_permitted_fieldnames` never lists a child table and testing for
		# membership dropped every one of them from every schema. The rows are
		# permission-checked where the permissions actually are — in
		# `_child_schema`, against the child's own permitted fieldnames. A table
		# above permlevel 0 is skipped rather than reasoned about.
		if cint(df.hidden) or cint(df.read_only) or cint(df.permlevel):
			continue
		rows.append(df)
		if len(rows) >= MAX_CHILD_TABLES:
			break
	return rows


def _child_schema(df: Any, parenttype: str, user: str) -> dict | None:
	child_meta = frappe.get_meta(df.options)
	# A child meta has no permissions of its own: called without parenttype it
	# returns an empty list, and the table would silently vanish from the schema.
	permitted = set(
		child_meta.get_permitted_fieldnames(parenttype=parenttype, user=user, permission_type="write")
	)
	sensitive = sensitive_fieldnames(df.options)
	chosen = _pick(child_meta, permitted, sensitive, MAX_CHILD_FIELDS)
	if not chosen:
		return None

	properties = {row.fieldname: _property(row) for row in chosen}
	fields = {
		row.fieldname: {
			"fieldtype": row.fieldtype,
			"options": row.options or "",
			"sensitive": row.fieldname in sensitive,
			"select_options": _select_options(row) if row.fieldtype == "Select" else [],
		}
		for row in chosen
	}

	return {
		"schema": {
			"type": "array",
			"description": f"{_(df.label or df.fieldname)}. {TABLE_NOTE}",
			"items": {
				"type": "object",
				"properties": properties,
				"required": sorted(properties),
				"additionalProperties": False,
			},
		},
		"fields": fields,
		"sensitive": sorted(name for name in sensitive if name in fields),
	}


def _property(df: Any) -> dict:
	if df.fieldtype == "Select":
		options = _select_options(df)
		if options:
			return {
				"type": "string",
				"enum": options,
				"description": _describe(
					df, 'One of the listed values, or "" if none of them is on the document.'
				),
			}
		return {"type": "string", "description": _describe(df, EMPTY_STRING_NOTE)}

	if df.fieldtype in INT_FIELDTYPES:
		note = "1 for yes, 0 for no." if df.fieldtype == "Check" else NUMBER_NOTE
		return {"type": "integer", "description": _describe(df, note)}

	if df.fieldtype in FLOAT_FIELDTYPES:
		return {"type": "number", "description": _describe(df, NUMBER_NOTE)}

	if df.fieldtype in ("Date", "Datetime"):
		shape = "yyyy-mm-dd" if df.fieldtype == "Date" else "yyyy-mm-dd HH:MM:SS"
		return {"type": "string", "description": _describe(df, f"Written as {shape}. {EMPTY_STRING_NOTE}")}

	if df.fieldtype == "Time":
		return {"type": "string", "description": _describe(df, f"Written as HH:MM:SS. {EMPTY_STRING_NOTE}")}

	if df.fieldtype in ("Link", "Dynamic Link"):
		linked = df.options if df.fieldtype == "Link" else "the linked record"
		return {
			"type": "string",
			"description": _describe(
				df,
				f"The name or title of {linked} as printed on the document — it is matched "
				f"against existing records later, so copy the text, do not invent an ID. "
				f"{EMPTY_STRING_NOTE}",
			),
		}

	hint = DATA_OPTION_HINTS.get(df.options or "") if df.fieldtype == "Data" else None
	return {
		"type": "string",
		"description": _describe(df, f"{hint} {EMPTY_STRING_NOTE}" if hint else EMPTY_STRING_NOTE),
	}


def _notes_property() -> dict:
	return {
		"type": "array",
		"description": _(
			"One entry per field you filled in, with how sure you are and which page you read "
			"it from. This is your own estimate: a person checks it."
		),
		"items": {
			"type": "object",
			"properties": {
				"fieldname": {"type": "string", "description": "The fieldname this note is about."},
				"confidence": {
					"type": "number",
					"description": "0 to 1. How sure you are that this value is what the document says.",
				},
				"page": {
					"type": "integer",
					"description": "1-indexed page you read the value from. 0 if you cannot say.",
				},
			},
			"required": ["fieldname", "confidence", "page"],
			"additionalProperties": False,
		},
	}


def _describe(df: Any, note: str) -> str:
	label = strip_html(str(_(df.label) if df.label else df.fieldname))[:LABEL_CHARS].strip()
	parts = [label]
	if df.description:
		parts.append(strip_html(str(df.description)).strip())
	parts.append(note)
	text = " ".join(part for part in parts if part)
	return " ".join(text.split())[:DESCRIPTION_CHARS]


def _select_options(df: Any) -> list[str]:
	"""Select options as an enum, with "" kept so the model can say "not stated"."""
	if not df.options:
		# A Select with no options is filled dynamically. An empty enum would make
		# every possible answer invalid, so it goes out as a plain string.
		return []
	options = [option.strip() for option in str(df.options).split("\n")]
	seen, cleaned = set(), []
	for option in options:
		if option in seen:
			continue
		seen.add(option)
		cleaned.append(option)
	if "" not in seen:
		cleaned.append("")
	return cleaned


def _coerce(meta: dict, value: Any) -> Any:
	fieldtype = meta.get("fieldtype")

	if fieldtype in INT_FIELDTYPES:
		return cint(value)
	if fieldtype in FLOAT_FIELDTYPES:
		return _number(value)

	if not isinstance(value, str):
		value = "" if value is None else str(value)
	value = value.strip()
	if not value:
		return None

	if fieldtype == "Select":
		options = meta.get("select_options") or []
		if options:
			# Enum casing is not guaranteed by any provider, so a returned option is
			# matched case-insensitively rather than written through as-is.
			for option in options:
				if option and option.lower() == value.lower():
					return option
			return None

	return value


def _number(value: Any) -> float:
	if isinstance(value, int | float):
		return float(value)
	try:
		return float(str(value or "").strip() or 0)
	except ValueError:
		return 0.0
