# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The sidebar patch: two links added, and nothing else touched.

An existing sidebar belongs to whoever has been using it. They may have renamed
a link, dropped one they never open, or moved a section — so the only safe way
to add Access Profiles and Blueprints to it is to append into the Build section
and leave every other row exactly where it was. Rebuilding the sidebar from the
shipped list would be the easy way to get the links in and would quietly undo
somebody's afternoon.
"""

import frappe

from frappe_agents.patches.v0_6_0.add_access_sidebar_links import (
	BUILD_SECTION,
	NEW_LINKS,
	SIDEBAR_DOCTYPE,
	execute,
)
from frappe_agents.tests.fixtures import AgentTestCase
from frappe_agents.tests.test_sidebar_coverage import build_test_sidebar


class SidebarCase(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		# Built here rather than borrowed from the site. This used to skip the
		# whole class when `app_sidebars()` came back empty — which is precisely
		# the site shape the sidebar work exists for, so the one case that
		# mattered was the one case never run. Building a throwaway sidebar
		# exercises the patch on every site, whatever it already has.
		self.sidebar = build_test_sidebar()
		self._strip_new_links()
		self.before = self.rows()

	def _strip_new_links(self) -> None:
		"""Put the sidebar back the way a site that never took this patch has it.

		A fresh build already carries the two links, so without this every
		assertion below would be about rows that were already there. Undone with
		the rest of the test's writes when the case rolls back.
		"""
		doc = frappe.get_doc(SIDEBAR_DOCTYPE, self.sidebar)
		targets = {link[1] for link in NEW_LINKS}
		keep = [row for row in doc.items if row.link_to not in targets]
		if len(keep) == len(doc.items):
			return

		doc.items = keep
		for idx, row in enumerate(doc.items, start=1):
			row.idx = idx
		doc.flags.ignore_links = True
		doc.save(ignore_permissions=True)

	def rows(self) -> list[tuple]:
		doc = frappe.get_doc(SIDEBAR_DOCTYPE, self.sidebar)
		return [(row.type, row.label, row.link_to) for row in doc.items]

	def build_section(self) -> list[tuple]:
		rows = self.rows()
		start = next(
			position + 1
			for position, row in enumerate(rows)
			if row[0] == "Section Break" and row[1] == BUILD_SECTION
		)
		section = []
		for row in rows[start:]:
			if row[0] == "Section Break":
				break
			section.append(row)
		return section


class TestAddingTheLinks(SidebarCase):
	def test_the_links_land_in_the_build_section(self):
		execute()

		targets = [row[2] for row in self.build_section()]
		for _label, link_to in NEW_LINKS:
			self.assertIn(link_to, targets)

	def test_nothing_that_was_there_is_disturbed(self):
		"""Append, never rebuild: every original row survives, in its own order."""
		execute()

		after = self.rows()
		kept = [row for row in after if row[2] not in {link[1] for link in NEW_LINKS}]
		self.assertEqual(kept, self.before)

	def test_running_it_again_adds_nothing(self):
		execute()
		once = self.rows()

		execute()

		self.assertEqual(self.rows(), once)
