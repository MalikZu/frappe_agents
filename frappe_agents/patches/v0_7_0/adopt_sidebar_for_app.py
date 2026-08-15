# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Make an existing sidebar the one the desk shows for this app's list views.

Opening any Agent list view on a site installed before this landed shows the
flat, hammer-icon "Frappe Agents" sidebar instead of the curated one. The desk
resolves a list view's sidebar by taking every sidebar that links the doctype,
narrowing that to the sidebars whose `app` matches the module's app, and falling
back to the auto-generated module sidebar when nothing is left. Two things put
this app's sidebar out of the running:

1. It was built without `app`, so the narrowing step drops it every time.
2. Agent Tool Call was never linked, so its list view was never a candidate at
   all — an unlinked doctype falls back whatever `app` says.

So this sets `app` when it is empty, and appends the Tool Calls link into the
Activity section. Both are append-and-adopt, never rebuild: an existing sidebar
belongs to whoever has been using it, and a sidebar that already carries an
`app` — because somebody set one — is left with the one they chose. Running it
twice changes nothing the second time.

Sidebars are found the same way `add_access_sidebar_links` finds them, by
module rather than name, because the desk seeds one under the workspace *title*
and the installer under the module name. A patch that knew only one would skip
half the estate.
"""

import frappe

from frappe_agents.install import ACTIVITY_SECTION, SIDEBAR_APP
from frappe_agents.patches.v0_7_0.add_access_sidebar_links import (
	SIDEBAR_DOCTYPE,
	app_sidebars,
)

# Appended after this link when the Activity section still has it, which is
# where a fresh install puts it: the run, then the calls the run made.
ANCHOR = "Agent Run"

NEW_LINK = ("Tool Calls", "Agent Tool Call")


def execute() -> None:
	for name in app_sidebars():
		_adopt(name)


def _adopt(name: str) -> None:
	doc = frappe.get_doc(SIDEBAR_DOCTYPE, name)

	changes = []
	if not doc.app:
		doc.app = SIDEBAR_APP
		changes.append(f"app={SIDEBAR_APP}")
	if _add_link(doc):
		changes.append(f"link to {NEW_LINK[1]}")
	if not changes:
		return

	# Link validation off, deliberately. Saving re-validates every row, including
	# the ones the desk seeded — and the desk's own "Home" row stores a workspace
	# *title* where a name belongs, which fails. Refusing an upgrade over a row
	# this patch did not write, and is not here to fix, is the wrong trade.
	doc.flags.ignore_permissions = True
	doc.flags.ignore_links = True

	# Same trade, one step further out: a sidebar is cosmetic and an upgrade is
	# not, so anything else this save dislikes rolls back to the savepoint and
	# leaves the sidebar exactly as it was. Only this save is undone.
	save_point = "frappe_agents_sidebar_app"
	frappe.db.savepoint(save_point)
	try:
		doc.save(ignore_permissions=True)
	except Exception as exc:
		frappe.db.rollback(save_point=save_point)
		print(f"frappe_agents: could not adopt sidebar {name} — {exc}")
		return

	frappe.db.release_savepoint(save_point)
	print(f"frappe_agents: sidebar {name} adopted ({', '.join(changes)})")


def _add_link(doc) -> bool:
	"""Put Tool Calls in the Activity section. False when there is nothing to do.

	Nothing to do covers three cases and they are all fine: the link is already
	there, the section was renamed or removed, or somebody moved the link
	somewhere else in the sidebar. Only its absence is a problem worth fixing.
	"""
	label, link_to = NEW_LINK
	present = {row.get("link_to") for row in doc.items if row.get("type") == "Link"}
	if link_to in present:
		return False

	section = _activity_section(doc)
	if section is None:
		return False

	start, end = section
	row = doc.append(
		"items",
		{
			"type": "Link",
			"label": label,
			"link_type": "DocType",
			"link_to": link_to,
			"child": 1,
			"collapsible": 1,
		},
	)
	doc.items.remove(row)
	doc.items.insert(_anchor_position(doc, start, end), row)

	for idx, item in enumerate(doc.items, start=1):
		item.idx = idx
	return True


def _activity_section(doc) -> tuple[int, int] | None:
	"""Where the Activity section's items start and end, or None if there is none.

	`start` is the first item after the Activity heading; `end` is one past its
	last item — the next heading, or the end of the sidebar.
	"""
	start = None
	for position, row in enumerate(doc.items):
		if row.get("type") != "Section Break":
			continue
		if start is not None:
			return start, position
		if (row.get("label") or "") == ACTIVITY_SECTION:
			start = position + 1

	if start is None:
		return None
	return start, len(doc.items)


def _anchor_position(doc, start: int, end: int) -> int:
	"""Just after the Runs link, or at the end of the section when it is gone."""
	for position in range(start, end):
		row = doc.items[position]
		if row.get("type") == "Link" and row.get("link_to") == ANCHOR:
			return position + 1
	return end
