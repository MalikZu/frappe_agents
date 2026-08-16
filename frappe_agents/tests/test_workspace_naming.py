# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The naming contract behind the desktop tile.

The desk stitches the tile to its destination through NAMES, not links: the
tile's label is lowercased and looked up against the Workspace Sidebar's
record name, and the workspace route is the slug of the workspace's record
name. A rename that only touches titles and labels therefore sends the tile
to a route that doesn't exist while everything else keeps working — exactly
the v0.5.0 regression. These tests read the shipped JSON and the installer
constants so the contract breaks in CI, not on a site.
"""

import json
import os
from unittest.mock import patch

import frappe

from frappe_agents.install import (
	SIDEBAR,
	WORKSPACE,
	build_workspace_sidebar,
	ensure_desktop_icon,
)
from frappe_agents.patches.v0_6_0.rename_workspace_to_agents import execute as rename_patch
from frappe_agents.tests.fixtures import AgentTestCase

APP_PATH = frappe.get_app_path("frappe_agents")


def _load(relative_path: str) -> dict:
	with open(os.path.join(APP_PATH, relative_path)) as f:
		return json.load(f)


class TestWorkspaceNamingContract(AgentTestCase):
	def test_workspace_name_title_and_label_agree(self):
		ws = _load(f"frappe_agents/workspace/{frappe.scrub(WORKSPACE)}/{frappe.scrub(WORKSPACE)}.json")
		self.assertEqual(ws["name"], WORKSPACE)
		self.assertEqual(ws["title"], WORKSPACE)
		self.assertEqual(ws["label"], WORKSPACE)

	def test_desktop_icon_label_matches_the_sidebar_name(self):
		icon = _load(f"desktop_icon/{frappe.scrub(WORKSPACE)}.json")
		# desktop.js: frappe.boot.workspace_sidebar_item[label.toLowerCase()]
		self.assertEqual(icon["label"], WORKSPACE)
		self.assertEqual(icon["link_to"], WORKSPACE)
		self.assertEqual(icon["name"], WORKSPACE)
		# restrict_removal: a deleted standard icon cascades (in developer mode it
		# deletes the shipped JSON from the app tree too) and nothing recreates it
		# until the next full sync. Hide the tile if unwanted; never delete it.
		self.assertEqual(icon["restrict_removal"], 1)

	def test_desktop_icon_filename_matches_its_scrubbed_label(self):
		# migrate's delete_duplicate_icons treats an icon as stale unless a file
		# named scrub(icon name).json exists under desktop_icon/ — a mismatched
		# filename gets the icon DELETED on the next migrate (and, in developer
		# mode, the JSON file itself deleted from the working tree).
		path = os.path.join(APP_PATH, "desktop_icon", f"{frappe.scrub(WORKSPACE)}.json")
		self.assertTrue(os.path.exists(path), path)

	def test_sidebar_home_link_targets_the_workspace(self):
		home = SIDEBAR[0]
		self.assertEqual(home[2], "Workspace")
		self.assertEqual(home[3], WORKSPACE)

	def test_apps_screen_route_slugs_the_workspace_name(self):
		entry = frappe.get_hooks("add_to_apps_screen", app_name="frappe_agents")[0]
		self.assertEqual(entry["route"], f"/desk/{frappe.scrub(WORKSPACE).replace('_', '-')}")

	def test_rename_patch_converges_a_half_renamed_site(self):
		# Build the v0.5.0 crime scene: old names, new labels.
		for doctype in ("Workspace", "Workspace Sidebar", "Desktop Icon"):
			if frappe.db.exists(doctype, WORKSPACE):
				frappe.rename_doc(doctype, WORKSPACE, "Frappe Agents", force=True)
		if not frappe.db.exists("Workspace Sidebar", "Frappe Agents"):
			build_workspace_sidebar()
			frappe.rename_doc("Workspace Sidebar", WORKSPACE, "Frappe Agents", force=True)
		frappe.db.set_value("Desktop Icon", "Frappe Agents", "label", WORKSPACE, update_modified=False)

		rename_patch()

		for doctype in ("Workspace", "Workspace Sidebar", "Desktop Icon"):
			self.assertTrue(frappe.db.exists(doctype, WORKSPACE), f"{doctype} not renamed to {WORKSPACE}")
			self.assertFalse(frappe.db.exists(doctype, "Frappe Agents"), f"old {doctype} row left behind")

		icon = frappe.db.get_value("Desktop Icon", WORKSPACE, ["label", "link_to"], as_dict=True)
		self.assertEqual(icon.label, WORKSPACE)
		self.assertEqual(icon.link_to, WORKSPACE)
		self.assertEqual(frappe.db.get_value("Workspace", WORKSPACE, "title"), WORKSPACE)
		self.assertEqual(frappe.db.get_value("Workspace Sidebar", WORKSPACE, "title"), WORKSPACE)

	def test_rename_patch_is_idempotent_on_a_converged_site(self):
		rename_patch()
		rename_patch()
		self.assertTrue(frappe.db.exists("Workspace", WORKSPACE))
		self.assertFalse(frappe.db.exists("Workspace", "Frappe Agents"))


class TestTheDeskTileHealsItself(AgentTestCase):
	"""The tile's row has gone missing four times, and nothing put it back.

	Model sync imports the shipped JSON early in a migrate, but migrate then
	deletes a standard Desktop Icon whose file it cannot find — and in developer
	mode deleting the row takes the JSON out of the app tree with it. The
	after_migrate self-heal is the floor under that.
	"""

	def test_it_leaves_the_tile_alone_when_it_is_there(self):
		self.assertTrue(frappe.db.exists("Desktop Icon", WORKSPACE))

		self.assertFalse(ensure_desktop_icon())

	def test_it_puts_the_tile_back_when_the_row_is_gone(self):
		# Deleted through the database on purpose. `Desktop Icon.on_trash` deletes
		# the shipped JSON out of the app tree in developer mode, which is one of
		# the ways this tile has gone missing before — a test must not be another.
		frappe.db.delete("Desktop Icon", {"name": WORKSPACE})
		self.assertFalse(frappe.db.exists("Desktop Icon", WORKSPACE))

		self.assertTrue(ensure_desktop_icon())

		icon = frappe.db.get_value(
			"Desktop Icon", WORKSPACE, ["label", "link_to", "link_type", "app"], as_dict=True
		)
		self.assertEqual(icon.label, WORKSPACE)
		self.assertEqual(icon.link_to, WORKSPACE)
		self.assertEqual(icon.link_type, "Workspace Sidebar")
		self.assertEqual(icon.app, "frappe_agents")

	def test_restoring_it_twice_leaves_one_tile(self):
		frappe.db.delete("Desktop Icon", {"name": WORKSPACE})

		ensure_desktop_icon()
		ensure_desktop_icon()

		self.assertEqual(len(frappe.get_all("Desktop Icon", filters={"name": WORKSPACE})), 1)

	def test_it_stops_rather_than_inventing_a_tile_with_no_shipped_json(self):
		"""Nothing to import is a thing to say, not a thing to guess at."""
		with patch("frappe_agents.install.WORKSPACE", "FA Tile With No File"):
			self.assertFalse(ensure_desktop_icon())

	def test_every_migrate_puts_the_tile_back(self):
		self.assertIn(
			"frappe_agents.install.ensure_desktop_icon",
			frappe.get_hooks("after_migrate", app_name="frappe_agents"),
		)
