# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The sidebar the desk actually shows for this app's list views.

Opening a list view does not show the sidebar the app ships unless two things
hold. The sidebar has to name the app it belongs to, because the desk narrows
the sidebars that link a doctype down to the ones whose `app` matches the
module's, and shows the auto-generated hammer-icon module sidebar when nothing
survives that. And the sidebar has to link *every* non-child doctype in the
module, because a doctype no sidebar links never reaches the narrowing step at
all — it falls back on its own.

So there are three questions here: a fresh install builds a sidebar that names
the app, the patch gives an older site the same thing without rebuilding it,
and no doctype in the module is missing its link. The last one is a contract:
it fails the day somebody adds a doctype and forgets, which is the only moment
the fix is cheap.
"""

from contextlib import contextmanager
from unittest.mock import patch

import frappe

from frappe_agents.access.exclusions import APP_MODULE
from frappe_agents.install import (
	ACTIVITY_SECTION,
	SIDEBAR,
	SIDEBAR_APP,
	build_workspace_sidebar,
)
from frappe_agents.patches.v0_6_0.add_access_sidebar_links import SIDEBAR_DOCTYPE, app_sidebars
from frappe_agents.patches.v0_6_0.adopt_sidebar_for_app import NEW_LINK, execute
from frappe_agents.patches.v0_6_1.build_workspace_sidebar import execute as build_patch
from frappe_agents.tests.fixtures import AgentTestCase

# Throwaway names for the sidebars these tests build. The real one is already on
# the site the tests run against, and `build_workspace_sidebar` refuses to touch
# a sidebar that exists — rightly, it is the user's. Building under another name
# exercises the same code and rolls back with the rest of the test. Both names
# have to move: the build now also refuses when the pre-rename name is taken.
TEST_SIDEBAR = "FA Test Sidebar"
TEST_OLD_SIDEBAR = "FA Test Sidebar Before The Rename"


@contextmanager
def throwaway_names():
	"""Point the installer's two sidebar names at names no site uses.

	Both constants are read at call time, which is the seam that lets a test
	exercise the real build on a site whose real sidebar must not be touched.
	"""
	with (
		patch("frappe_agents.install.WORKSPACE", TEST_SIDEBAR),
		patch("frappe_agents.install.SIDEBAR_NAME", TEST_OLD_SIDEBAR),
	):
		yield


def build_test_sidebar() -> str:
	with throwaway_names():
		build_workspace_sidebar()
	return TEST_SIDEBAR


class SidebarBuildCase(AgentTestCase):
	def build(self) -> str:
		return build_test_sidebar()

	def rows(self, name: str) -> list[tuple]:
		doc = frappe.get_doc(SIDEBAR_DOCTYPE, name)
		return [(row.type, row.label, row.link_to) for row in doc.items]

	def activity_section(self, name: str) -> list[tuple]:
		rows = self.rows(name)
		start = next(
			position + 1
			for position, row in enumerate(rows)
			if row[0] == "Section Break" and row[1] == ACTIVITY_SECTION
		)
		section = []
		for row in rows[start:]:
			if row[0] == "Section Break":
				break
			section.append(row)
		return section


class TestTheSidebarAFreshInstallBuilds(SidebarBuildCase):
	def test_it_names_the_app_it_belongs_to(self):
		"""Without this the desk drops it and shows the auto module sidebar."""
		name = self.build()

		self.assertEqual(frappe.get_doc(SIDEBAR_DOCTYPE, name).app, SIDEBAR_APP)

	def test_it_names_the_module_the_sidebar_patches_search_by(self):
		"""`app_sidebars` filters on module, so an empty one is invisible forever."""
		name = self.build()

		self.assertEqual(frappe.get_doc(SIDEBAR_DOCTYPE, name).module, APP_MODULE)

	def test_it_leaves_a_sidebar_under_the_pre_rename_name_alone(self):
		"""Building beside it would give the module two public sidebars."""
		with (
			patch("frappe_agents.install.WORKSPACE", TEST_OLD_SIDEBAR),
			patch("frappe_agents.install.SIDEBAR_NAME", TEST_OLD_SIDEBAR),
		):
			build_workspace_sidebar()

		with throwaway_names():
			self.assertIsNone(build_workspace_sidebar())
		self.assertFalse(frappe.db.exists(SIDEBAR_DOCTYPE, TEST_SIDEBAR))


class TestGivingASidebarToASiteThatOnlyEverMigrated(SidebarBuildCase):
	"""The shape that broke: no Workspace Sidebar row on the site at all.

	`build_workspace_sidebar` only ever ran from `after_install`, so a site
	installed before it landed has no row, the desk tile that resolves against
	that row throws, and the two patches that only adopt an existing sidebar
	iterate over nothing and report success.
	"""

	def run_patch(self) -> None:
		with throwaway_names():
			build_patch()

	def test_it_builds_the_sidebar(self):
		self.assertFalse(frappe.db.exists(SIDEBAR_DOCTYPE, TEST_SIDEBAR))

		self.run_patch()

		self.assertTrue(frappe.db.exists(SIDEBAR_DOCTYPE, TEST_SIDEBAR))

	def test_what_it_builds_is_the_sidebar_the_desk_shows(self):
		self.run_patch()

		doc = frappe.get_doc(SIDEBAR_DOCTYPE, TEST_SIDEBAR)
		self.assertEqual(doc.app, SIDEBAR_APP)
		self.assertEqual(doc.module, APP_MODULE)

	def test_the_patches_that_only_adopt_can_find_what_it_built(self):
		self.run_patch()

		self.assertIn(TEST_SIDEBAR, app_sidebars())

	def test_running_it_again_changes_nothing(self):
		self.run_patch()
		once = self.rows(TEST_SIDEBAR)

		self.run_patch()

		self.assertEqual(self.rows(TEST_SIDEBAR), once)

	def test_it_leaves_an_existing_sidebar_exactly_as_it_was(self):
		self.build()
		doc = frappe.get_doc(SIDEBAR_DOCTYPE, TEST_SIDEBAR)
		doc.items = doc.items[:3]
		doc.flags.ignore_links = True
		doc.save(ignore_permissions=True)
		before = self.rows(TEST_SIDEBAR)

		self.run_patch()

		self.assertEqual(self.rows(TEST_SIDEBAR), before)

	def test_a_build_that_fails_does_not_take_the_migrate_down(self):
		"""A sidebar is cosmetic; a customer's migrate is not."""
		with patch(
			"frappe_agents.install.build_workspace_sidebar",
			side_effect=Exception("link validation said no"),
		):
			self.run_patch()

		self.assertFalse(frappe.db.exists(SIDEBAR_DOCTYPE, TEST_SIDEBAR))


class TestTheSidebarIsRebuiltByEveryMigrate(AgentTestCase):
	"""The patch fixes today's estate once; the hook is the floor after that."""

	def hooks(self) -> list[str]:
		return frappe.get_hooks("after_migrate", app_name="frappe_agents")

	def test_the_tool_registry_still_syncs_first(self):
		self.assertEqual(self.hooks()[0], "frappe_agents.tools.registry.sync_tools")

	def test_the_sidebar_self_heal_runs_on_every_migrate(self):
		self.assertIn("frappe_agents.install.ensure_workspace_sidebar", self.hooks())


class TestAdoptingASidebarBuiltBeforeTheFix(SidebarBuildCase):
	"""The patch, against a sidebar shaped the way a pre-fix site has one."""

	def setUp(self) -> None:
		super().setUp()
		self.sidebar = self.build()
		self._make_it_pre_fix()
		self.before = self.rows(self.sidebar)

	def _make_it_pre_fix(self) -> None:
		"""Strip the app and the Tool Calls link back off the built sidebar."""
		doc = frappe.get_doc(SIDEBAR_DOCTYPE, self.sidebar)
		doc.app = None
		doc.items = [row for row in doc.items if row.link_to != NEW_LINK[1]]
		for idx, row in enumerate(doc.items, start=1):
			row.idx = idx
		doc.flags.ignore_links = True
		doc.save(ignore_permissions=True)

	def test_the_app_is_set(self):
		execute()

		self.assertEqual(frappe.get_doc(SIDEBAR_DOCTYPE, self.sidebar).app, SIDEBAR_APP)

	def test_tool_calls_lands_in_the_activity_section(self):
		execute()

		self.assertIn(NEW_LINK[1], [row[2] for row in self.activity_section(self.sidebar)])

	def test_nothing_that_was_there_is_disturbed(self):
		"""Append, never rebuild: every original row survives, in its own order."""
		execute()

		kept = [row for row in self.rows(self.sidebar) if row[2] != NEW_LINK[1]]
		self.assertEqual(kept, self.before)

	def test_running_it_again_changes_nothing(self):
		execute()
		once = self.rows(self.sidebar)

		execute()

		self.assertEqual(self.rows(self.sidebar), once)
		self.assertEqual(frappe.get_doc(SIDEBAR_DOCTYPE, self.sidebar).app, SIDEBAR_APP)


class TestEveryDoctypeIsInTheSidebar(AgentTestCase):
	# Doctypes deliberately kept out of the sidebar. Empty, and adding to it is a
	# decision: whatever goes in here shows the auto-generated module sidebar
	# instead of this app's, for anyone who opens its list view.
	EXCEPTIONS = frozenset()

	def test_no_module_doctype_is_missing_its_link(self):
		listed = {row[3] for row in SIDEBAR if row[0] == "Link" and row[2] == "DocType"}
		on_site = set(frappe.get_all("DocType", filters={"module": APP_MODULE, "istable": 0}, pluck="name"))

		self.assertEqual(
			on_site - self.EXCEPTIONS,
			listed,
			msg=(
				"install.SIDEBAR must link every non-child doctype in the module. The desk "
				"picks a list view's sidebar from the sidebars that link that doctype, so an "
				"unlinked one falls back to the auto-generated 'Frappe Agents' module sidebar "
				"— the flat hammer-icon list — instead of this app's. Add a Link row for the "
				"new doctype, or name it in EXCEPTIONS here and accept the fallback."
			),
		)
