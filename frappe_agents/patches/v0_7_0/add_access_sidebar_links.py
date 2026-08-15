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

The sidebar is found by module rather than by name. The desk seeds one for a
workspace under the workspace's *title*, so this app's sidebar is called
"Agents" on a site that let the desk make it and "Frappe Agents" on one where
the installer got there first. Both are the same sidebar, and a patch that knew
only one name would silently skip half the estate.
"""

import frappe

from frappe_agents.access.exclusions import APP_MODULE
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
	for name in app_sidebars():
		_add_links(name)


def app_sidebars() -> list[str]:
	"""This app's public sidebars, whichever of the two names they carry.

	A sidebar with `for_user` set is somebody's personal copy and is left alone:
	they made it theirs on purpose.
	"""
	names = frappe.get_all(
		SIDEBAR_DOCTYPE,
		filters={"module": APP_MODULE, "for_user": ("in", ("", None))},
		pluck="name",
	)
	if SIDEBAR_NAME not in names and frappe.db.exists(SIDEBAR_DOCTYPE, SIDEBAR_NAME):
		names.append(SIDEBAR_NAME)
	return names


def _add_links(name: str) -> None:
	doc = frappe.get_doc(SIDEBAR_DOCTYPE, name)
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

	# Link validation off, deliberately. Saving re-validates every row, including
	# the ones the desk seeded — and the desk's own "Home" row stores a workspace
	# *title* where a name belongs, which fails. Refusing an upgrade over a row
	# this patch did not write, and is not here to fix, is the wrong trade.
	doc.flags.ignore_permissions = True
	doc.flags.ignore_links = True

	# Same trade, one step further out: a sidebar link is cosmetic and an upgrade
	# is not, so anything else this save dislikes rolls back to the savepoint and
	# leaves the sidebar exactly as it was. Only this save is undone.
	save_point = "frappe_agents_sidebar_links"
	frappe.db.savepoint(save_point)
	try:
		doc.save(ignore_permissions=True)
	except Exception as exc:
		frappe.db.rollback(save_point=save_point)
		print(f"frappe_agents: could not add sidebar links to {name} — {exc}")
		return

	frappe.db.release_savepoint(save_point)
	print(f"frappe_agents: added {len(missing)} sidebar link(s) to {name}")


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
