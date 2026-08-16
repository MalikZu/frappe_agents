# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""patches.txt is a list of import paths, and nothing else checks them.

Frappe reads the file at migrate time and calls `<string>.execute` for every line
it has not already logged. A line that names a module which is not there raises
mid-migrate, on a customer's site, with the schema half-synced — and the whole
test suite is green right up until that moment, because tests import the patch
modules directly and never read the file. A rename that missed a line is exactly
the failure this catches, which is why it was written alongside one.
"""

import frappe
from frappe.modules.patch_handler import PatchType, get_patches_from_app

from frappe_agents.patches.v0_6_1.backfill_renamed_patch_log import RENAMED
from frappe_agents.tests.fixtures import AgentTestCase

APP = "frappe_agents"


class TestEveryPatchInTheFileExists(AgentTestCase):
	def test_every_listed_patch_has_an_execute(self):
		listed = get_patches_from_app(APP)
		self.assertTrue(listed, "patches.txt parsed as empty — the section headers are wrong")

		for patch in listed:
			with self.subTest(patch=patch):
				# Exactly what frappe does in execute_patch before calling it.
				frappe.get_attr(f"{patch}.execute")

	def test_no_patch_is_listed_twice(self):
		listed = get_patches_from_app(APP)

		self.assertEqual(sorted(listed), sorted(set(listed)))


class TestTheRenamedPatchesAreCoveredByTheBackfill(AgentTestCase):
	"""The backfill's map and the file have to agree, or the rename half-lands."""

	def test_the_backfill_runs_before_the_patches_it_covers(self):
		pre = get_patches_from_app(APP, patch_type=PatchType.pre_model_sync)

		self.assertEqual(
			pre[0],
			"frappe_agents.patches.v0_6_1.backfill_renamed_patch_log",
			msg=(
				"the backfill has to record the renamed patches as executed before frappe "
				"reads the patch log to decide what to run, so it must be the first entry"
			),
		)

	def test_every_new_name_it_records_is_a_patch_the_app_ships(self):
		listed = set(get_patches_from_app(APP))

		for old, new in RENAMED.items():
			with self.subTest(patch=old):
				self.assertIn(
					new,
					listed,
					msg=f"the backfill records {new} as executed but patches.txt never lists it",
				)

	def test_no_old_name_is_still_listed(self):
		listed = set(get_patches_from_app(APP))

		self.assertEqual(listed & set(RENAMED), set())
