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

Reading a *schema* is a narrower question, and it gets its own predicate.
`is_describable` is `is_excluded` widened by exactly one thing: the blueprint's
own child tables, the tables a blueprint is written out of. It is asked from one
place — the Builder's `describe_site_doctype` — and it is never a grant. Nothing
it admits may be named by a rule row, listed as a target, or reached by a tool
that returns a document. Keeping the two predicates apart is what leaves every
gate that asks `is_excluded` unchanged by construction rather than by review.

Two things every name in here is put through first, because a gate that can be
walked around is not a gate:

* **Casing.** `tabDocType.name` collates case-insensitively, so `user` and `User`
  are one doctype to the database and to `frappe.get_meta`. A frozenset that only
  knew `User` made `is_excluded("user")` False, and everything downstream —
  validation, the compiled grant, the schema tool — believed it. Every name is
  canonicalised through frappe's own spelling before it is compared.
* **Customization.** The widening is derived from what this app *ships* on the
  blueprint, not from its live meta. Live meta carries Custom Fields and Property
  Setters, so anything able to add a field to Agent Blueprint could otherwise
  have named the doctype whose schema the Builder may read.
"""

from typing import Any

import frappe
from frappe.model import table_fields
from frappe.utils import cint

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

# The same names, folded. The lookup has to be case-insensitive because every
# other lookup on a doctype name already is: `frappe.get_meta("user")` returns
# User and `tabDocType` matches it too. A case-sensitive membership test made
# `is_excluded("user")` False, which was a live grant on User for anyone who
# wrote the target in lower case, and a describe that answered for `user` while
# refusing `User` — the refusal oracle these tools promise does not exist.
CORE_SECURITY_FOLDED = frozenset(name.casefold() for name in CORE_SECURITY_DOCTYPES)


def is_excluded(doctype: str | None) -> bool:
	"""Whether this doctype is off limits to every agent, always.

	Fails closed: no doctype at all is excluded, because a caller that lost the
	target should not be handed a grant. Asked of the site's own spelling of the
	name, so the answer cannot be changed by changing the casing.
	"""
	doctype = (doctype or "").strip()
	if not doctype:
		return True

	# One meta lookup answers both halves. The Agents Builder asks this question
	# once per doctype on the site, so the two questions share the read.
	meta = doctype_meta(doctype)
	name = meta.name if meta is not None else doctype
	if name == BLUEPRINT:
		return False
	if name.casefold() in CORE_SECURITY_FOLDED:
		return True
	return (meta.module if meta is not None else None) == APP_MODULE


def canonical_doctype(doctype: str | None) -> str | None:
	"""The site's own spelling of this name, or None when there is no such doctype.

	Every lookup that reaches the database already treats `user` and `User` as one
	doctype, so anything deciding *policy* about a name has to agree with the
	database rather than with the string it was handed. Asking frappe what the
	doctype is really called is how it agrees.
	"""
	meta = doctype_meta(doctype)
	return meta.name if meta is not None else None


def module_of(doctype: str) -> str | None:
	"""The module a doctype belongs to, or None when there is no such doctype."""
	meta = doctype_meta(doctype)
	return meta.module if meta is not None else None


def doctype_meta(doctype: str | None) -> Any:
	"""This doctype's meta, or None when the site has no such doctype.

	Read off the meta rather than the DocType document. Both are cached, but the
	document is the whole definition — fields, permissions and all — and the
	Agents Builder asks about every doctype on the site. On a stock site that is
	the difference between two seconds and none.
	"""
	try:
		return frappe.get_meta(doctype)
	except Exception:
		return None


def describable_child_tables() -> frozenset[str]:
	"""The blueprint's own child tables, by name. Derived, never listed.

	A blueprint cannot be written without naming the fields of the table its
	suggested rules live in, so that table is the one shape the Builder has to be
	able to read. Deriving the set means a second child table added to the
	blueprint next release is covered the day it lands — the same discipline that
	makes the exclusion itself computed rather than listed.

	Derived from the field definitions this app **ships**, not from the live meta.
	Meta is the shipped fields plus every Custom Field and Property Setter on the
	site, so reading it here meant anything that could hang a Table field off Agent
	Blueprint — another app, an implementer, a row in `tabCustom Field` — got to
	choose a doctype whose schema the Builder would read and whose read permission
	`describe_site_doctype` would then ask of Agent Blueprint instead. A
	customization must not be able to widen an exclusion.

	Each name is then held to being one of this app's own child tables, so a
	DocField row that came from anywhere else buys nothing either. Scoped to the
	blueprint on purpose: "any child table this app ships" would also hand over
	the shape of an agent's own tool selection and role gating, which is precisely
	what an agent may not see.
	"""
	try:
		named = frappe.get_all(
			"DocField",
			filters={
				"parent": BLUEPRINT,
				"parenttype": "DocType",
				"fieldtype": ("in", table_fields),
			},
			pluck="options",
		)
	except Exception:
		return frozenset()

	return frozenset(name for name in named if name and is_own_child_table(name))


def is_own_child_table(doctype: str) -> bool:
	"""Whether this is a child table belonging to this app. The widening's floor.

	Both halves matter. `istable` is what makes a doctype ungrantable and
	unreadable by any tool that returns a document, which is the reason describing
	one gives nothing away. The module is what makes it already excluded, so
	admitting it widens the schema door and nothing else.
	"""
	meta = doctype_meta(doctype)
	return meta is not None and bool(cint(meta.istable)) and meta.module == APP_MODULE


def is_blueprint_child_table(doctype: str | None) -> bool:
	"""Whether this names one of the blueprint's own child tables, in any casing."""
	name = canonical_doctype(doctype)
	return bool(name) and name in describable_child_tables()


def is_describable(doctype: str | None) -> bool:
	"""Whether the Builder may read this doctype's fields. Never a grant.

	Wider than `is_excluded` by the blueprint's child tables and by nothing else.
	A child table is written through its parent and can never be a rule target, so
	describing one says nothing that describing the parent did not already say —
	while not describing it left the Builder guessing at the fieldnames of its own
	output. Fails closed on no doctype at all, the same as `is_excluded`.
	"""
	doctype = (doctype or "").strip()
	if not doctype:
		return False
	if not is_excluded(doctype):
		return True
	return is_blueprint_child_table(doctype)


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
