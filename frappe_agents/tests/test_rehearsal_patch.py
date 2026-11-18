# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The rehearsals patch: two links, each in its own place, and nothing else moved.

Rehearsals goes into Build right after Agents. Rehearsal Results goes at the end
of Activity. A sidebar somebody has reshaped keeps its shape: a missing section
costs only its own link.

The whole case runs with `frappe.flags.in_patch` set, which is how the patch
runs for real. Newer v16 builds keep Workspace Sidebar as an archive that refuses
edits outside migrate, so building the test sidebar needs the flag too.
"""

import frappe

from frappe_agents.install import ACTIVITY_SECTION, BUILD_SECTION, sidebars_supported
from frappe_agents.patches.v0_6_0.add_access_sidebar_links import SIDEBAR_DOCTYPE
from frappe_agents.patches.v0_7_0.add_rehearsals import NEW_LINKS, execute
from frappe_agents.tests.fixtures import AgentTestCase
from frappe_agents.tests.test_sidebar_coverage import build_test_sidebar

TARGETS = {link_to for _section, _anchor, _label, link_to in NEW_LINKS}


class TestAddingTheRehearsalLinks(AgentTestCase):
	def setUp(self) -> None:
		# frappe_agents patch (version-15): Workspace Sidebar is v16-only.
		if not sidebars_supported():
			self.skipTest("Workspace Sidebar is not in this Frappe version")
		super().setUp()
		in_patch = frappe.flags.in_patch
		frappe.flags.in_patch = True
		self.addCleanup(setattr, frappe.flags, "in_patch", in_patch)

		self.sidebar = build_test_sidebar()
		self._edit(lambda items: [row for row in items if row.link_to not in TARGETS])
		self.before = self.rows()

	def _edit(self, change) -> None:
		"""Rewrite the test sidebar's rows; the rollback after the test undoes it."""
		doc = frappe.get_doc(SIDEBAR_DOCTYPE, self.sidebar)
		doc.items = change(doc.items)
		for idx, row in enumerate(doc.items, start=1):
			row.idx = idx
		doc.flags.ignore_links = True
		doc.save(ignore_permissions=True)

	def rows(self) -> list[tuple]:
		doc = frappe.get_doc(SIDEBAR_DOCTYPE, self.sidebar)
		return [(row.type, row.label, row.link_to) for row in doc.items]

	def section(self, label: str) -> list[tuple]:
		rows = self.rows()
		start = next(i + 1 for i, row in enumerate(rows) if row[0] == "Section Break" and row[1] == label)
		section = []
		for row in rows[start:]:
			if row[0] == "Section Break":
				break
			section.append(row)
		return section

	def test_each_link_lands_in_its_place(self):
		execute()

		build = [row[2] for row in self.section(BUILD_SECTION)]
		self.assertEqual(build[build.index("Agent") + 1], "Agent Rehearsal")
		self.assertEqual(self.section(ACTIVITY_SECTION)[-1][2], "Agent Rehearsal Result")

	def test_nothing_that_was_there_is_disturbed(self):
		execute()

		kept = [row for row in self.rows() if row[2] not in TARGETS]
		self.assertEqual(kept, self.before)

	def test_running_it_again_adds_nothing(self):
		execute()
		once = self.rows()

		execute()

		self.assertEqual(self.rows(), once)

	def test_a_sidebar_without_activity_still_gets_the_build_link(self):
		def drop_activity(items):
			kept, skipping = [], False
			for row in items:
				if row.type == "Section Break":
					skipping = row.label == ACTIVITY_SECTION
				if not skipping:
					kept.append(row)
			return kept

		self._edit(drop_activity)

		execute()

		links = [row[2] for row in self.rows()]
		self.assertIn("Agent Rehearsal", links)
		self.assertNotIn("Agent Rehearsal Result", links)
