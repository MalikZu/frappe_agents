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

from unittest.mock import patch

import frappe

from frappe_agents.access.exclusions import APP_MODULE
from frappe_agents.install import ACTIVITY_SECTION, SIDEBAR, SIDEBAR_APP, build_workspace_sidebar
from frappe_agents.patches.v0_7_0.add_access_sidebar_links import SIDEBAR_DOCTYPE
from frappe_agents.patches.v0_7_0.adopt_sidebar_for_app import NEW_LINK, execute
from frappe_agents.install import sidebars_supported
from frappe_agents.tests.fixtures import AgentTestCase

# A throwaway name for the sidebar these tests build. The real one is already on
# the site the tests run against, and `build_workspace_sidebar` refuses to touch
# a sidebar that exists — rightly, it is the user's. Building under another name
# exercises the same code and rolls back with the rest of the test.
TEST_SIDEBAR = "FA Test Sidebar"


class SidebarBuildCase(AgentTestCase):
	def setUp(self) -> None:
		# frappe_agents patch (version-15): Workspace Sidebar is v16-only. These
		# assertions describe a doctype this framework does not have.
		if not sidebars_supported():
			self.skipTest("Workspace Sidebar is not in this Frappe version")

	def build(self) -> str:
		with patch("frappe_agents.install.WORKSPACE", TEST_SIDEBAR):
			build_workspace_sidebar()
		return TEST_SIDEBAR

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
