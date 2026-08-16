# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Make every record the desktop tile touches agree on the name "Agents".

The v0.5.0 rename changed titles and labels to "Agents" but left the record
names at "Frappe Agents". Names are not internal here: the desk resolves the
desktop tile by matching Desktop Icon.label against the Workspace Sidebar's
NAME (lowercased), and routes the workspace by slugging its record NAME — so
the half-rename sent the tile to /app/agents, a page that did not exist,
while the sidebar (which slugs the name) kept working.

Runs pre_model_sync on purpose: the renamed workspace and desktop-icon JSON
files sync right after this patch, and they must find an already-renamed row
to update — renaming after the sync would leave a duplicate "Agents" record
next to the old one.
"""

import frappe

from frappe_agents.install import desktop_tile_supported, sidebars_supported

OLD = "Frappe Agents"
NEW = "Agents"


def execute():
	# frappe_agents patch (version-15): Workspace Sidebar is v16-only.
	doctypes = ["Workspace", "Desktop Icon"]
	if sidebars_supported():
		doctypes.insert(1, "Workspace Sidebar")
	for doctype in doctypes:
		if not frappe.db.exists(doctype, OLD):
			continue
		if frappe.db.exists(doctype, NEW):
			# A half-migrated site already synced a new-name row; the old one
			# is the leftover.
			frappe.delete_doc(doctype, OLD, ignore_permissions=True, force=True)
		else:
			frappe.rename_doc(doctype, OLD, NEW, force=True)

	# rename_doc moves the docname; the display fields and the icon's link
	# target still carry the old word on a site that installed before v0.5.0
	# (or where the JSON sync skipped on an unchanged modified stamp).
	if frappe.db.exists("Workspace", NEW):
		frappe.db.set_value("Workspace", NEW, {"title": NEW, "label": NEW}, update_modified=False)
	if sidebars_supported() and frappe.db.exists("Workspace Sidebar", NEW):
		frappe.db.set_value("Workspace Sidebar", NEW, "title", NEW, update_modified=False)
	if desktop_tile_supported() and frappe.db.exists("Desktop Icon", NEW):
		frappe.db.set_value("Desktop Icon", NEW, {"label": NEW, "link_to": NEW}, update_modified=False)
