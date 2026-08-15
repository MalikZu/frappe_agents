# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""One row of the access matrix: a target, the verbs allowed on it, and its caps.

Frappe does not run a child controller's `validate`, so the check below is a
module function every parent calls — Agent Access Profile, Agent, Agent
Blueprint. That keeps one definition of what a legal rule is, which is the whole
point of the matrix: a row that would grant something impossible is refused when
it is written, not discovered when a tool refuses at run time.

Validation is the first of the two exclusion checks. The second is in
`require_grant`, because a row written by direct SQL never passes through here.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from frappe_agents.access.exclusions import (
	exclusion_reason,
	is_excluded,
	report_exclusion_reason,
	report_is_excluded,
)

TARGET_DOCTYPE = "DocType"
TARGET_REPORT = "Report"
TARGET_TYPES = (TARGET_DOCTYPE, TARGET_REPORT)

# The verbs that write. A single settings document and a child table are neither
# drafted nor submitted, so a rule that claims otherwise is a mistake.
DRAFT_CHECKS = ("can_create_draft", "can_update_draft", "can_propose", "can_extract")
CHECKS = ("can_read", *DRAFT_CHECKS)

# Checks a report row does not have. A report is run or it is not.
REPORT_ONLY = ("can_read",)


class AgentAccessRule(Document):
	pass


def validate_rules(doc: Document, fieldname: str = "rules") -> None:
	"""Validate every rule row in one table. Parents call this from `validate`."""
	for row in doc.get(fieldname) or []:
		validate_rule(row)


def validate_rule(row: Document) -> None:
	"""Refuse a row that could not mean anything, or must never mean anything."""
	target_type = (row.get("target_type") or "").strip()
	target = (row.get("target") or "").strip()

	if target_type not in TARGET_TYPES:
		frappe.throw(_("An access rule targets a DocType or a Report."))
	if not target:
		frappe.throw(_("An access rule needs a target."))
	if not frappe.db.exists(target_type, target):
		frappe.throw(_("There is no {0} called {1}.").format(target_type, target))

	if cint(row.get("max_rows_per_call")) < 0:
		frappe.throw(_("Max Rows Per Call cannot be negative."))

	if target_type == TARGET_REPORT:
		_validate_report_row(row, target)
		return

	_validate_doctype_row(row, target)


def _validate_report_row(row: Document, target: str) -> None:
	"""A report row grants running it. The other checks are cleared, not honoured."""
	# A report is a way of reading the doctype under it, so the exclusions apply
	# here too — otherwise the list of doctypes no agent may name would be a lock
	# on one door only.
	if report_is_excluded(target):
		frappe.throw(report_exclusion_reason(target))

	if not cint(row.get("can_read")):
		frappe.throw(
			_("The rule for report {0} grants nothing. Tick Read to let the agent run it.").format(target)
		)
	for check in CHECKS:
		if check not in REPORT_ONLY:
			row.set(check, 0)
	row.set("update_any_draft", 0)


def _validate_doctype_row(row: Document, target: str) -> None:
	if is_excluded(target):
		frappe.throw(exclusion_reason(target))

	if not any(cint(row.get(check)) for check in CHECKS):
		frappe.throw(
			_(
				"The rule for {0} grants nothing. Tick at least one of Read, Draft New, Draft Edit, "
				"Propose or Extract."
			).format(target)
		)

	meta = frappe.get_meta(target)
	if not any(cint(row.get(check)) for check in DRAFT_CHECKS):
		return

	if cint(meta.istable):
		frappe.throw(
			_("{0} is a child table. Grant its parent doctype instead — rows are written through it.").format(
				target
			)
		)
	if cint(meta.issingle):
		frappe.throw(_("{0} is a single settings document, so there is nothing to draft.").format(target))
