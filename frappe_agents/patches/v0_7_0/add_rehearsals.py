# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Give sites that already exist the Agent Rehearsal User role and two sidebar links.

A fresh install gets both from `install`. An existing sidebar is the user's, so
this appends and never rebuilds: Rehearsals goes into the Build section after
the Agents link, Rehearsal Results at the end of the Activity section. A link
that is already there, or a section that is gone, is left alone. Running it
twice adds nothing the second time.
"""

import frappe

from frappe_agents.install import ACTIVITY_SECTION, BUILD_SECTION, create_roles, sidebars_supported
from frappe_agents.patches.v0_6_0.add_access_sidebar_links import SIDEBAR_DOCTYPE, app_sidebars

# (section, anchor link_to or None for the end of the section, label, link_to)
NEW_LINKS = (
	(BUILD_SECTION, "Agent", "Rehearsals", "Agent Rehearsal"),
	(ACTIVITY_SECTION, None, "Rehearsal Results", "Agent Rehearsal Result"),
)


def execute() -> None:
	create_roles()
	# frappe_agents patch (version-15): no sidebar doctype, nothing to link.
	if not sidebars_supported():
		return
	for name in app_sidebars():
		_add_links(name)


def _add_links(name: str) -> None:
	doc = frappe.get_doc(SIDEBAR_DOCTYPE, name)
	present = {row.get("link_to") for row in doc.items if row.get("type") == "Link"}
	added = 0
	for section, anchor, label, link_to in NEW_LINKS:
		if link_to in present:
			continue
		at = _position(doc, section, anchor)
		if at is None:
			continue
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
		doc.items.insert(at, row)
		added += 1
	if not added:
		return

	for idx, row in enumerate(doc.items, start=1):
		row.idx = idx

	# The same trade the earlier sidebar patch makes: a sidebar link is cosmetic
	# and an upgrade is not. Link validation stays off, because the desk's own
	# Home row fails it, and anything else this save dislikes rolls back alone.
	doc.flags.ignore_permissions = True
	doc.flags.ignore_links = True
	save_point = "frappe_agents_rehearsal_links"
	frappe.db.savepoint(save_point)
	try:
		doc.save(ignore_permissions=True)
	except Exception as exc:
		frappe.db.rollback(save_point=save_point)
		print(f"frappe_agents: could not add rehearsal links to {name} — {exc}")
		return
	frappe.db.release_savepoint(save_point)


def _position(doc, section: str, anchor: str | None) -> int | None:
	"""Where to insert: after the anchor, else at the end of the section. None if no section."""
	start = end = None
	for position, row in enumerate(doc.items):
		if row.get("type") != "Section Break":
			continue
		if start is not None:
			end = position
			break
		if (row.get("label") or "") == section:
			start = position + 1
	if start is None:
		return None
	end = len(doc.items) if end is None else end
	if anchor:
		for position in range(start, end):
			row = doc.items[position]
			if row.get("type") == "Link" and row.get("link_to") == anchor:
				return position + 1
	return end
