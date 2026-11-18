# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Field changes on a live record: which fields a proposal may touch, and how values compare.

A live record is a document of a doctype with no submit step: a ticket, a lead,
a task. It sits at docstatus 0 for its whole life, so "draft" means nothing for
it, and an edit to it is an edit to the record the business works from. Those
changes go through an Update proposal, and this module holds the rules both
ends of that proposal share: the tool that records it, and the approval that
applies it.

Three choices in here are deliberate:

* **An allowlist of fieldtypes, not a denylist.** A proposal shows the approver
  a before and an after, and every fieldtype here renders as plain text that
  says exactly what it is. Rich text hides links and images behind the words,
  and a table is a set of rows with its own permissions and its own staleness.
  Those are left out until each has its own answer.
* **One normaliser, keyed on the field.** The same comparison decides "is this a
  change at all", "did this field move since the agent looked", and "did the
  save write what was approved". `1` and `"1"`, `10` and `10.0`, `None` and `""`
  are the same value to a person, and they must be the same value here too, or
  a proposal fails for a difference nobody can see.
* **A refusal never repeats a value.** The rules run for a user who may not read
  the field; telling them what it holds would be the leak the rule exists for.
"""

import datetime
import json
import math
import re
from typing import Any

import frappe
from frappe.model import default_fields
from frappe.model.workflow import get_workflow, get_workflow_name
from frappe.utils import cint, flt, get_datetime, get_time, getdate

from frappe_agents.extraction.gate import sensitive_fieldnames

# Fieldtypes a proposal may change. Each one is a single value that reads the
# same as plain text as it does in the form.
TEXT_TYPES = frozenset({"Data", "Select", "Link", "Small Text", "Text", "Long Text"})
INT_TYPES = frozenset({"Int", "Duration"})
FLOAT_TYPES = frozenset({"Float", "Currency", "Percent", "Rating"})
ALLOWED_FIELDTYPES = TEXT_TYPES | INT_TYPES | FLOAT_TYPES | {"Check", "Date", "Datetime", "Time"}

# What one proposal may change at once. Past this, it is not a change an
# approver can read in one look.
MAX_FIELDS = 20

# Identity, ownership and the record's own bookkeeping. `default_fields` is the
# framework's list; the rest are fields a doctype may declare that still decide
# what the document is rather than what it says.
FORBIDDEN_FIELDS = frozenset(default_fields) | {"naming_series", "amended_from", "workflow_state"}

INTEGER = re.compile(r"^[+-]?\d+$")


class UnusableValue(ValueError):
	"""A proposed value that is not a value of its field's type."""


def live_problem(meta: Any) -> str | None:
	"""Why documents of this doctype are not live records, if they are not."""
	if cint(meta.istable):
		return f"{meta.name} is a child table. Propose a change to the parent document instead."
	if cint(meta.issingle):
		return f"{meta.name} is a single settings document, not a record to change."
	if cint(meta.is_submittable):
		return (
			f"{meta.name} has a submit step, so its drafts are edited with update_draft and its "
			"submitted documents are changed by cancelling and amending. propose_update is for "
			"records with no submit step."
		)
	return None


def field_refusal(meta: Any, fieldname: Any, doc: Any) -> str | None:
	"""Why a proposal may not change this field, judged for the session user.

	Run twice: when the agent proposes, as the user the run acts as, and when a
	person approves, as the approver — the doctype can change in between, and a
	field that turned sensitive or read-only since is refused on the second pass.
	"""
	if not isinstance(fieldname, str) or not fieldname:
		return "Field names must be strings."
	if fieldname in FORBIDDEN_FIELDS or fieldname.startswith("_"):
		return f"{fieldname} is a system field. A proposal cannot change it."

	df = meta.get_field(fieldname)
	if not df:
		return f"{meta.name} has no field {fieldname}."
	if df.fieldtype not in ALLOWED_FIELDTYPES:
		return (
			f"{fieldname} is a {df.fieldtype} field. A proposal can change plain values only: "
			"text, numbers, dates, checkboxes, selects and links."
		)
	if cint(df.get("is_virtual")):
		return f"{fieldname} is computed, not stored. There is nothing to change."
	if cint(df.hidden) or cint(df.read_only):
		return f"{fieldname} is not editable on the form, so a proposal cannot change it either."
	if fieldname in sensitive_fields(meta):
		return f"{fieldname} is marked sensitive. A person changes it by hand."
	if fieldname == workflow_state_field(meta.name):
		return f"{fieldname} is the workflow state. Propose a workflow action with propose_transition instead."
	if cint(df.set_only_once) and not is_blank(doc.get(fieldname)):
		return f"{fieldname} can only be set once, and it is already set."
	return None


def sensitive_fields(meta: Any) -> set[str]:
	"""Fields an administrator marked sensitive, or masked for the session user.

	Masking is per user and arrived in frappe v16; on a framework without it,
	only the Agent Settings list applies.
	"""
	fields = set(sensitive_fieldnames(meta.name))
	try:
		fields |= {df.fieldname for df in meta.get_masked_fields()}
	except AttributeError:
		pass
	return fields


def workflow_state_field(doctype: str) -> str | None:
	"""The field an active Workflow keeps its state in, or None with no Workflow."""
	if not get_workflow_name(doctype):
		return None
	return get_workflow(doctype).workflow_state_field


def normalise(df: Any, value: Any, precision: int | None = None) -> Any:
	"""`value` as the one form two equal values of this field share.

	Raises `UnusableValue` for input that is not a value of the field's type,
	rather than coercing it: `cint("abc")` is 0, and a proposal to set a count to
	zero because the model wrote a word is not one anybody made.
	"""
	fieldtype = df.fieldtype
	if fieldtype == "Check":
		return 1 if _integer(value) else 0
	if fieldtype in INT_TYPES:
		return _integer(value)
	if fieldtype in FLOAT_TYPES:
		return flt(_number(value), precision)
	if fieldtype == "Date":
		return None if is_blank(value) else _parsed(getdate, value).isoformat()
	if fieldtype == "Datetime":
		return None if is_blank(value) else _parsed(get_datetime, value).isoformat(" ")
	if fieldtype == "Time":
		return None if is_blank(value) else _parsed(get_time, value).isoformat()
	return _text(value)


def same_value(df: Any, left: Any, right: Any, precision: int | None = None) -> bool:
	"""Whether two values of this field are the same value."""
	try:
		return normalise(df, left, precision) == normalise(df, right, precision)
	except UnusableValue:
		return False


def precision_of(doc: Any, df: Any) -> int | None:
	"""The rounding the framework applies to this field, for number fields only."""
	if df.fieldtype not in FLOAT_TYPES:
		return None
	return cint(doc.precision(df.fieldname)) or None


def is_blank(value: Any) -> bool:
	return value is None or (isinstance(value, str) and not value.strip())


def dumps(values: dict) -> str:
	return json.dumps(values, sort_keys=True, separators=(",", ":"))


def loads(stored: Any) -> dict:
	"""A stored JSON field back as a dict. Anything unreadable is an empty change."""
	if isinstance(stored, dict):
		return stored
	if not isinstance(stored, str) or not stored.strip():
		return {}
	try:
		value = json.loads(stored)
	except ValueError:
		return {}
	return value if isinstance(value, dict) else {}


def _integer(value: Any) -> int:
	if is_blank(value):
		return 0
	if isinstance(value, bool):
		return int(value)
	if isinstance(value, int):
		return value
	if isinstance(value, float) and value.is_integer():
		return int(value)
	if isinstance(value, str) and INTEGER.match(value.strip()):
		return int(value.strip())
	raise UnusableValue("expected a whole number")


def _number(value: Any) -> float:
	if is_blank(value):
		return 0.0
	if isinstance(value, bool):
		raise UnusableValue("expected a number")
	try:
		number = float(value.strip() if isinstance(value, str) else value)
	except (TypeError, ValueError):
		raise UnusableValue("expected a number")
	if not math.isfinite(number):
		raise UnusableValue("expected a finite number")
	return number


def _parsed(parse: Any, value: Any) -> Any:
	if not isinstance(value, str | datetime.date | datetime.time | datetime.timedelta):
		raise UnusableValue("expected a date or time")
	try:
		return parse(value)
	except Exception:
		raise UnusableValue("expected a date or time in ISO form, such as 2026-11-18 or 14:30:00")


def _text(value: Any) -> str:
	if value is None:
		return ""
	if isinstance(value, bool):
		raise UnusableValue("expected text")
	if isinstance(value, int | float):
		return str(value)
	if not isinstance(value, str):
		raise UnusableValue("expected text")
	return value.strip()
