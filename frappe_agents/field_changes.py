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
INT_TYPES = frozenset({"Int"})
# Duration is seconds in a decimal column, so a fraction of a second is a value.
FLOAT_TYPES = frozenset({"Float", "Currency", "Percent", "Rating", "Duration"})
ALLOWED_FIELDTYPES = TEXT_TYPES | INT_TYPES | FLOAT_TYPES | {"Check", "Date", "Datetime", "Time"}

# What one proposal may change at once. Past this, it is not a change an
# approver can read in one look.
MAX_FIELDS = 20

# Identity, ownership and the record's own bookkeeping. `default_fields` is the
# framework's list; the rest are fields a doctype may declare that still decide
# what the document is rather than what it says.
FORBIDDEN_FIELDS = frozenset(default_fields) | {"naming_series", "amended_from", "workflow_state"}

INTEGER = re.compile(r"^[+-]?\d+$")
# What a Float, Currency or Percent column keeps: decimal(21,9).
STORED_DECIMALS = 9
# Named so the formatter cannot rewrite the except clause into syntax version-15 cannot parse.
NOT_A_NUMBER = (TypeError, ValueError)


class UnusableValue(ValueError):
	"""A proposed value that is not a value of its field's type."""


def live_problem(meta: Any) -> str | None:
	"""Why documents of this doctype are not live records, if they are not."""
	if cint(meta.istable):
		return f"{meta.name} is a child table. Propose a change to the parent document instead."
	if cint(meta.issingle):
		return f"{meta.name} is a single settings document, not a record to change."
	if cint(meta.read_only):
		return f"{meta.name} is read-only. Its records are not changed by hand, so not by a proposal either."
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
	# Before the editability checks: the field a Workflow creates for its state is
	# hidden, and "not editable" would send the agent nowhere useful.
	if fieldname == workflow_state_field(meta.name):
		return (
			f"{fieldname} is the workflow state. Propose a workflow action with propose_transition instead."
		)
	if cint(df.get("is_virtual")):
		return f"{fieldname} is computed, not stored. There is nothing to change."
	# A condition that makes a field read-only is evaluated in the browser, against
	# the form; the server cannot tell whether it holds, so it assumes it might.
	if cint(df.hidden) or cint(df.read_only) or df.get("read_only_depends_on"):
		return f"{fieldname} is not editable on the form, so a proposal cannot change it either."
	if fieldname in sensitive_fields(meta):
		return f"{fieldname} is marked sensitive. A person changes it by hand."
	if cint(df.set_only_once) and not is_blank(doc.get(fieldname)):
		return f"{fieldname} can only be set once, and it is already set."
	return None


def has_field_access(doc: Any, df: Any, permission_type: str) -> bool:
	"""Whether the session user may read or write this field, beyond the document.

	Permlevel 0 is the document's own right, already proved by `has_permission`
	— which also counts shares and permission hooks. Asking the role table about
	level 0 would refuse a user whose access comes from a share.
	"""
	if not cint(df.permlevel):
		return True
	return doc.has_permlevel_access_to(df.fieldname, df, permission_type)


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
		return None if is_blank(value) else _parsed("Date", value).isoformat()
	if fieldtype == "Datetime":
		return None if is_blank(value) else _parsed("Datetime", value).isoformat(" ")
	if fieldtype == "Time":
		return None if is_blank(value) else _parsed("Time", value).isoformat()
	return _text(value)


def same_value(df: Any, left: Any, right: Any, precision: int | None = None) -> bool:
	"""Whether two values of this field are the same value."""
	try:
		return normalise(df, left, precision) == normalise(df, right, precision)
	except UnusableValue:
		return False


def precision_of(doc: Any, df: Any) -> int | None:
	"""The decimals a number field keeps: its own precision, else the column's nine.

	Not the system's display precision: frappe does not round a float to it when
	saving, so rounding to it here would refuse 3.6725 against 3.67251 as no change.
	"""
	if df.fieldtype not in FLOAT_TYPES:
		return None
	return cint(df.precision) or STORED_DECIMALS


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
	except NOT_A_NUMBER:
		raise UnusableValue("expected a number")
	if not math.isfinite(number):
		raise UnusableValue("expected a finite number")
	return number


def _parsed(fieldtype: str, value: Any) -> Any:
	"""A date, datetime or time. Strings must be ISO: frappe's own parsers guess at
	other forms and raise through `frappe.throw`, which leaves a message behind."""
	if isinstance(value, str):
		text = value.strip()
		try:
			if fieldtype == "Date":
				parsed = datetime.date.fromisoformat(text)
			elif fieldtype == "Datetime":
				parsed = datetime.datetime.fromisoformat(text)
			else:
				parsed = datetime.time.fromisoformat(text)
		except ValueError:
			raise UnusableValue("expected a date or time in ISO form, such as 2026-11-18 or 14:30:00")
		# frappe stores local wall-clock time with no zone. An offset would be
		# written as text the column cannot hold, or silently mean another hour.
		if getattr(parsed, "tzinfo", None) is not None:
			raise UnusableValue(
				"expected a local date or time with no time zone, such as 2026-11-18 14:30:00"
			)
		return parsed
	if fieldtype == "Date" and isinstance(value, datetime.date):
		return getdate(value)
	if fieldtype == "Datetime" and isinstance(value, datetime.date):
		return get_datetime(value)
	if fieldtype == "Time" and isinstance(value, datetime.time | datetime.timedelta | datetime.datetime):
		return get_time(value)
	raise UnusableValue("expected a date or time")


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
