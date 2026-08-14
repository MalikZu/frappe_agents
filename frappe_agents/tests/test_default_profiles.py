# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The profiles a site is given, and the promise that seeding keeps its hands off.

Seeding runs twice on a real site — once at install, once again through the
patch — and on the second run somebody may already have edited a profile. The
tests below are about that: what is shipped, that a second run creates nothing,
and that an edited profile survives a migration untouched. A seed that reasserts
itself would silently rewrite what a live agent may do.

The profiles are also asserted to be inert. Seeding grants nothing to anybody
until a manager attaches a profile to an agent, so a fresh install adds no
access at all.
"""

import frappe

from frappe_agents.access.default_profiles import (
	DEFAULT_PROFILES,
	PERSONAL_ORGANIZER,
	PROFILE_DOCTYPE,
	SITE_READER,
	seed_default_profiles,
)
from frappe_agents.access.grants import compiled_grants
from frappe_agents.tests.fixtures import AgentTestCase, make_matrix_agent

SHIPPED = (PERSONAL_ORGANIZER, SITE_READER)


class TestDefaultProfiles(AgentTestCase):
	def profile(self, name: str):
		return frappe.get_doc(PROFILE_DOCTYPE, name)

	def rows(self, name: str) -> dict:
		return {row.target: row for row in self.profile(name).rules}

	def reseed(self) -> list[str]:
		return seed_default_profiles()

	def test_both_core_profiles_are_present(self):
		"""Installed by after_install, and by the patch on a site that predates it."""
		for name in SHIPPED:
			self.assertTrue(frappe.db.exists(PROFILE_DOCTYPE, name), name)

	def test_the_personal_organizer_reads_and_drafts_the_desk_doctypes(self):
		rows = self.rows(PERSONAL_ORGANIZER)

		self.assertEqual(set(rows), {"ToDo", "Note", "Event", "Contact"})
		for row in rows.values():
			self.assertTrue(row.can_read)
			self.assertTrue(row.can_create_draft)
			self.assertFalse(row.can_propose)
			self.assertFalse(row.can_extract)

	def test_the_organizer_edits_only_its_own_drafts(self):
		rows = self.rows(PERSONAL_ORGANIZER)

		self.assertEqual({name for name, row in rows.items() if row.can_update_draft}, {"ToDo", "Note"})
		self.assertEqual({row.update_any_draft for row in rows.values()}, {0})

	def test_the_site_reader_only_reads(self):
		rows = self.rows(SITE_READER)

		self.assertEqual(set(rows), {"ToDo", "Note", "Contact", "Address"})
		for row in rows.values():
			self.assertTrue(row.can_read)
			self.assertFalse(row.can_create_draft)
			self.assertFalse(row.can_update_draft)
			self.assertFalse(row.can_propose)
			self.assertFalse(row.can_extract)

	def test_seeding_again_creates_nothing(self):
		self.assertEqual(self.reseed(), [])

	def test_seeding_again_never_clobbers_an_edited_profile(self):
		"""Someone narrowed the shipped profile. A migration must not undo that."""
		profile = self.profile(SITE_READER)
		profile.set("rules", [{"target_type": "DocType", "target": "ToDo", "can_read": 1}])
		profile.description = "Narrowed by the site."
		profile.save(ignore_permissions=True)

		self.reseed()

		kept = self.profile(SITE_READER)
		self.assertEqual([row.target for row in kept.rules], ["ToDo"])
		self.assertEqual(kept.description, "Narrowed by the site.")

	def test_a_seeded_profile_grants_nothing_until_an_agent_carries_it(self):
		"""Inert by construction: writing a profile is not granting access."""
		nobody = make_matrix_agent([])
		self.assertEqual(compiled_grants(nobody)["DocType"], {})

		holder = make_matrix_agent([], profiles=[SITE_READER])
		self.assertEqual(set(compiled_grants(holder)["DocType"]), {"ToDo", "Note", "Contact", "Address"})

	def test_every_shipped_rule_names_a_doctype_this_site_has(self):
		"""A rule for a missing doctype would be refused at validation."""
		for spec in DEFAULT_PROFILES:
			for row in spec["rules"]:
				self.assertTrue(frappe.db.exists("DocType", row["target"]), row["target"])
