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
from contextlib import contextmanager
from unittest.mock import patch

import frappe

from frappe_agents.install import (
	SIDEBAR,
	WORKSPACE,
	build_workspace_sidebar,
	desktop_icon_fields,
	desktop_icon_path,
	ensure_desktop_icon,
)
from frappe_agents.patches.v0_6_0.rename_workspace_to_agents import execute as rename_patch
from frappe_agents.install import sidebars_supported
from frappe_agents.tests.fixtures import AgentTestCase

APP_PATH = frappe.get_app_path("frappe_agents")

# What the shipped desktop-icon JSON carries that the rebuild deliberately does
# not: sync bookkeeping, the name `set_name` supplies, and an empty child table.
NOT_REBUILT = {"doctype", "modified", "name", "roles"}


def _load(relative_path: str) -> dict:
	with open(os.path.join(APP_PATH, relative_path)) as f:
		return json.load(f)


class TestWorkspaceNamingContract(AgentTestCase):
	def setUp(self):
		# frappe_agents patch (version-15): Workspace Sidebar is v16-only. These
		# assertions describe a doctype this framework does not have.
		if not sidebars_supported():
			self.skipTest("Workspace Sidebar is not in this Frappe version")
		super().setUp()
		self.refuse_to_delete_the_shipped_tile_file()

	def refuse_to_delete_the_shipped_tile_file(self):
		"""Stop a rename in here from taking the app's own shipped JSON with it.

		`DesktopIcon.after_rename` calls `delete_desktop_icon_file(app, old_name)`
		UNCONDITIONALLY — developer mode or not — and only writes the new name's
		file back when developer mode is on. So renaming the standard tile on an
		ordinary bench deletes `desktop_icon/agents.json` from the app tree and
		puts nothing back, and the rollback in `AgentTestCase.setUp` does not undo
		a filesystem write. Proven at frappe/desk/doctype/desktop_icon/
		desktop_icon.py `after_rename`, on a site with `developer_mode: 0`.

		That made this module corrupt its own working tree: green on the first run
		and red on every run after, with a deleted tracked file in `git status`.
		It is the deleter behind three of the failures this class used to show, and
		one of the ways the tile has gone missing four times.

		Refused rather than repaired afterwards: there is then no window in which
		the file is absent, and a crash mid-test cannot leave the tree damaged.
		Nothing this class asserts is about frappe's file export — the renames here
		exist to build the v0.5.0 crime scene — so refusing that one side effect
		takes nothing away from what the tests prove.
		"""
		patcher = patch("frappe.desk.doctype.desktop_icon.desktop_icon.delete_desktop_icon_file")
		patcher.start()
		self.addCleanup(patcher.stop)

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
		# migrate's remove_orphan_entities treats a standard icon as an orphan
		# unless a file named scrub(icon name).json exists under desktop_icon/ —
		# a mismatched filename gets the icon DELETED on the next migrate, with
		# force=True, and restrict_removal does not stop it.
		path = os.path.join(APP_PATH, "desktop_icon", f"{frappe.scrub(WORKSPACE)}.json")
		self.assertTrue(os.path.exists(path), path)
		# and install.py must go looking in the same place
		self.assertEqual(desktop_icon_path(), path)

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

		# The renames above must not have taken the app's shipped JSON with them.
		# See refuse_to_delete_the_shipped_tile_file: without that guard this test
		# deletes a tracked file and every later run of this module fails.
		self.assertTrue(os.path.exists(desktop_icon_path()), "the rename deleted the shipped tile file")

	def test_rename_patch_is_idempotent_on_a_converged_site(self):
		rename_patch()
		rename_patch()
		self.assertTrue(frappe.db.exists("Workspace", WORKSPACE))
		self.assertFalse(frappe.db.exists("Workspace", "Frappe Agents"))


class TestTheDeskTileHealsItself(AgentTestCase):
	"""The tile's row has gone missing four times, and nothing put it back.

	Model sync imports the shipped JSON early in a migrate; `remove_orphan_entities`
	later in the same migrate deletes every standard Desktop Icon whose file it
	cannot find, with force=True and no regard for `restrict_removal` (nothing in
	frappe enforces that flag server-side). Lose the file — `after_rename` deletes
	it whatever the mode, `on_trash` in developer mode — and the row is deleted
	again on every migrate for ever. The after_migrate self-heal is the floor.

	None of these tests deletes the shipped file to make its point: the fixture
	family here is the Desktop Icon ROW, and a filename that was never shipped
	stands in for the file being gone. The JSON is a tracked artifact of the app,
	not test scratch space.
	"""

	@contextmanager
	def the_shipped_file_is_gone(self):
		"""Point install.py at a filename that was never shipped.

		The other way to reach this branch — deleting `desktop_icon/agents.json`
		for the duration of the test — is the exact working-tree corruption this
		module was just fixed for. Redirecting the lookup proves the same branch,
		leaves the tracked file alone, and keeps WORKSPACE real, so the rebuilt row
		is the real tile validated against the real Workspace Sidebar rather than a
		throwaway name nothing on the site links to.
		"""
		never_shipped = os.path.join(APP_PATH, "desktop_icon", "fa_never_shipped.json")
		self.assertFalse(os.path.exists(never_shipped), never_shipped)
		with patch("frappe_agents.install.desktop_icon_path", return_value=never_shipped):
			yield

	def keep_the_shipped_tile_file_byte_for_byte(self) -> str:
		"""Put the shipped JSON back if a test in here manages to rewrite it."""
		path = desktop_icon_path()
		with open(path, "rb") as f:
			shipped = f.read()

		def put_it_back():
			if os.path.exists(path):
				with open(path, "rb") as f:
					if f.read() == shipped:
						return
			with open(path, "wb") as f:
				f.write(shipped)

		self.addCleanup(put_it_back)
		return path

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

	def test_it_prefers_the_shipped_file_over_its_own_constants(self):
		"""The released file is the source of truth wherever there is one.

		The stubbed import puts no row back, which is also how frappe reports a
		skipped import — by returning False, not by raising. Saying it worked on
		that would be the self-heal lying about the one thing it is for.
		"""
		frappe.db.delete("Desktop Icon", {"name": WORKSPACE})

		with patch("frappe.modules.import_file.import_file_by_path") as imported:
			self.assertFalse(ensure_desktop_icon())

		imported.assert_called_once()
		self.assertEqual(imported.call_args.args[0], desktop_icon_path())
		self.assertFalse(frappe.db.exists("Desktop Icon", WORKSPACE))

	def test_restoring_it_twice_leaves_one_tile(self):
		frappe.db.delete("Desktop Icon", {"name": WORKSPACE})

		ensure_desktop_icon()
		ensure_desktop_icon()

		self.assertEqual(len(frappe.get_all("Desktop Icon", filters={"name": WORKSPACE})), 1)

	def test_it_rebuilds_the_tile_when_the_shipped_json_is_gone_too(self):
		"""The one state that cannot recover on its own is the one to act in.

		With no file, model sync has nothing to import and remove_orphan_entities
		deletes the row again on every migrate — a site stuck there never gets its
		tile back. So the row is rebuilt from constants. The app tree is still not
		written to: what is missing is a row, and a migrate has no business
		authoring the app's released files.

		The rebuilt row is the shipped tile in every field that matters, so a site
		healed this way is a site that can be migrated back onto the real file.
		"""
		frappe.db.delete("Desktop Icon", {"name": WORKSPACE})

		with self.the_shipped_file_is_gone():
			self.assertTrue(ensure_desktop_icon())

		icon = frappe.db.get_value(
			"Desktop Icon",
			WORKSPACE,
			["label", "link_to", "link_type", "app", "icon_type", "logo_url", "standard", "restrict_removal"],
			as_dict=True,
		)
		self.assertEqual(icon.label, WORKSPACE)
		self.assertEqual(icon.link_to, WORKSPACE)
		self.assertEqual(icon.link_type, "Workspace Sidebar")
		self.assertEqual(icon.app, "frappe_agents")
		self.assertEqual(icon.icon_type, desktop_icon_fields()["icon_type"])
		self.assertEqual(icon.logo_url, desktop_icon_fields()["logo_url"])
		# standard, or the desk shows the tile to its owner alone.
		self.assertEqual(icon.standard, 1)
		self.assertEqual(icon.restrict_removal, 1)

	def test_rebuilding_it_writes_nothing_into_the_app_tree(self):
		"""Not even in developer mode, where frappe would export it for us.

		`DesktopIcon.on_update` writes a standard icon back to `desktop_icon/` when
		developer mode is on. A migrate regenerating a released file is how the two
		copies drift, and on a bench it is a spurious diff in a tracked file. The
		rebuild suppresses it, and this is what says the suppression is deliberate.
		"""
		path = self.keep_the_shipped_tile_file_byte_for_byte()
		folder = os.path.dirname(path)
		before = (sorted(os.listdir(folder)), os.stat(path).st_mtime_ns)
		frappe.db.delete("Desktop Icon", {"name": WORKSPACE})

		with patch.dict(frappe.conf, {"developer_mode": 1}), self.the_shipped_file_is_gone():
			self.assertTrue(ensure_desktop_icon())

		self.assertEqual((sorted(os.listdir(folder)), os.stat(path).st_mtime_ns), before)

	def test_the_rebuild_constants_still_match_the_shipped_file(self):
		"""Two copies of the tile, so CI is what notices when they disagree."""
		shipped = _load(f"desktop_icon/{frappe.scrub(WORKSPACE)}.json")
		fields = desktop_icon_fields()

		for field, value in fields.items():
			self.assertEqual(shipped.get(field), value, f"{field} drifted from the shipped JSON")

		# and the other direction, or a field added to the JSON is simply dropped
		# from every rebuilt tile without anything saying so.
		self.assertEqual(
			set(shipped) - NOT_REBUILT - set(fields),
			set(),
			"the shipped JSON grew a field the rebuild does not set",
		)

	def test_every_migrate_puts_the_tile_back(self):
		self.assertIn(
			"frappe_agents.install.ensure_desktop_icon",
			frappe.get_hooks("after_migrate", app_name="frappe_agents"),
		)
