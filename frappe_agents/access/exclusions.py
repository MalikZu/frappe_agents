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
	"""The module a doctype belongs to, or None when there is no such doctype."""
	try:
		return frappe.get_cached_value("DocType", doctype, "module")
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
