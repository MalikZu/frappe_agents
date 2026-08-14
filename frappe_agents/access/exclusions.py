# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Doctypes no access rule may ever name, whatever a row says.

Two sets, and the first one is computed rather than listed:

1. **Everything this app ships.** An agent that could write Agent, Agent Access
   Profile or Agent Tool Call could widen its own grant or edit the record of
   what it did. Deriving the set from the module means a governance doctype
   added next release is excluded the day it lands, with nobody having to
   remember. `Agent Blueprint` is the one deliberate hole: it is what the
   Builder writes, and a blueprint grants nothing until a human materialises it.
2. **The site's own security surface**, named one by one. Users, roles,
   permissions, scripts, workflows: the records that decide who may do what.
   An agent that edits those is not using its permissions, it is rewriting them.

The check runs twice — once when a rule row is saved, once again inside
`require_grant` at call time — so a row put in by direct SQL still denies.

Reports are asked the same question through the doctype they read. A report over
User is a way to read User, and an exclusion that only watched the DocType rows
would be a lock on the front door with the side door open.
"""

import frappe

APP_MODULE = "Frappe Agents"

# The Builder's one write surface. A blueprint is a proposal on paper: it holds
# suggested rules, and only a human with Agent Manager turns it into an agent.
BLUEPRINT = "Agent Blueprint"

CORE_SECURITY_DOCTYPES = frozenset(
	{
		"User",
		"Role",
		"Role Profile",
		"DocPerm",
		"Custom DocPerm",
		"User Permission",
		"System Settings",
		"Module Def",
		"Server Script",
		"Client Script",
		"Custom Field",
		"Property Setter",
		"Workflow",
		"Workflow State",
		"Workflow Action Master",
		"Installed Applications",
		"DocType",
	}
)


def is_excluded(doctype: str | None) -> bool:
	"""Whether this doctype is off limits to every agent, always.

	Fails closed: no doctype at all is excluded, because a caller that lost the
	target should not be handed a grant.
	"""
	doctype = (doctype or "").strip()
	if not doctype:
		return True
	if doctype == BLUEPRINT:
		return False
	if doctype in CORE_SECURITY_DOCTYPES:
		return True
	return module_of(doctype) == APP_MODULE


def module_of(doctype: str) -> str | None:
	"""The module a doctype belongs to, or None when there is no such doctype.

	Read off the meta rather than the DocType document. Both are cached, but the
	document is the whole definition — fields, permissions and all — and the
	Agents Builder asks this question once per doctype on the site. On a stock
	site that is the difference between two seconds and none.
	"""
	try:
		return frappe.get_meta(doctype).module
	except Exception:
		return None


def report_is_excluded(report: str | None) -> bool:
	"""Whether this report reads a doctype no agent may ever be granted.

	A report row grants running one report, and running one returns rows of the
	doctype it reports on. A rule naming a report over User or over this app's own
	records would hand an agent exactly what `is_excluded` refuses — the same
	records, through a second door — so the exclusion is asked of the report's
	`ref_doctype` as well.

	A report that names no doctype is not excluded here: there is nothing to ask
	the question of. Such a report still passes the user's own permission checks
	in `run_report`, which is the floor it always had.
	"""
	ref = report_ref_doctype(report)
	return bool(ref) and is_excluded(ref)


def report_ref_doctype(report: str | None) -> str | None:
	"""The doctype a report reads, or None when it names none or does not exist."""
	report = (report or "").strip()
	if not report:
		return None
	try:
		return frappe.get_cached_value("Report", report, "ref_doctype") or None
	except Exception:
		return None


def exclusion_reason(doctype: str) -> str:
	"""Why this doctype is refused, in words a model and a person both read."""
	if module_of(doctype) == APP_MODULE:
		return (
			f"{doctype} is part of the agent framework itself. Agents cannot be granted "
			"access to what governs them."
		)
	return f"{doctype} is part of the site's security configuration and is never granted to an agent."


def report_exclusion_reason(report: str) -> str:
	"""Why this report is refused: the doctype underneath it is."""
	ref = report_ref_doctype(report) or ""
	return f"The report {report} reads {ref}. {exclusion_reason(ref)}"
