# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Put Access Profiles and Blueprints in the sidebar of sites that already have one.

A fresh install gets them from `install.build_workspace_sidebar`. A site
installed before the matrix has a sidebar already, and that sidebar is the
user's: they may have renamed a link, dropped one, or moved a whole section.
So this appends and never rebuilds — two links added inside the Build section,
in the order a fresh install ships them, and nothing else touched.

It does nothing at all when there is no sidebar, when there is no Build section
to add them under, or when the links are already there. Running it twice adds
nothing the second time.
"""

import frappe

from frappe_agents.install import BUILD_SECTION, SIDEBAR_NAME

SIDEBAR_DOCTYPE = "Workspace Sidebar"

# Appended after this link when the Build section still has it, which is where a
# fresh install puts them: the agent, then what an agent is made of.
ANCHOR = "Agent"

NEW_LINKS = (
	("Access Profiles", "Agent Access Profile"),
	("Blueprints", "Agent Blueprint"),
)


def execute() -> None:
	if not frappe.db.exists(SIDEBAR_DOCTYPE, SIDEBAR_NAME):
		return

	doc = frappe.get_doc(SIDEBAR_DOCTYPE, SIDEBAR_NAME)
	section = _build_section(doc)
	if section is None:
		return

	start, end = section
	present = {row.get("link_to") for row in doc.items if row.get("type") == "Link"}
	missing = [link for link in NEW_LINKS if link[1] not in present]
	if not missing:
		return

	at = _anchor_position(doc, start, end)
	for offset, (label, link_to) in enumerate(missing):
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
		doc.items.insert(at + offset, row)

	for idx, row in enumerate(doc.items, start=1):
		row.idx = idx

	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)
	print(f"frappe_agents: added {len(missing)} sidebar link(s) to {SIDEBAR_NAME}")


def _build_section(doc) -> tuple[int, int] | None:
	"""Where the Build section's items start and end, or None if there is none.

	`start` is the first item after the Build heading; `end` is one past its last
	item — the next heading, or the end of the sidebar.
	"""
	start = None
	for position, row in enumerate(doc.items):
		if row.get("type") != "Section Break":
			continue
		if start is not None:
			return start, position
		if (row.get("label") or "") == BUILD_SECTION:
			start = position + 1

	if start is None:
		return None
	return start, len(doc.items)


def _anchor_position(doc, start: int, end: int) -> int:
	"""Just after the Agent link, or at the end of the section when it is gone."""
	for position in range(start, end):
		row = doc.items[position]
		if row.get("type") == "Link" and row.get("link_to") == ANCHOR:
			return position + 1
	return end
